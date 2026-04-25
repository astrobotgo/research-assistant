"""
Context agent: synthesizes a week of past daily briefings into a concise
historical-context block that the main digest agent can use to:
  - ground recurring themes and open questions
  - avoid re-selecting papers already featured recently
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.agents import COPERNICUS
from app.gemini_llm import gemini_generate
from app.summarize import OLLAMA_HOST, OLLAMA_MODEL


def _extract_json(text: str):
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass
    return None


def _read_past_digests(days_back: int, today: date) -> list[tuple[str, str]]:
    """Return [(date_str, digest_text)] for recent days that have .md reports."""
    results = []
    for offset in range(days_back, 0, -1):  # oldest first so history reads forward
        d = today - timedelta(days=offset)
        ds = d.isoformat()
        md_path = Path(f"data/reports/daily-{ds}.md")
        if not md_path.exists():
            continue
        text = md_path.read_text()
        # Pull the synthesized briefing section only (between the two known headers)
        m = re.search(
            r"## Research briefing \(synthesized\)\n\n(.*?)(?=\n## Catalog|\Z)",
            text,
            flags=re.DOTALL,
        )
        excerpt = m.group(1).strip() if m else text[:3000]
        results.append((ds, excerpt))
    return results


def _read_recent_selected_papers(
    days_back: int,
    today: date,
) -> tuple[set[str], list[str], list[dict]]:
    """Return recent selected IDs, titles, and lightweight paper records."""
    covered_ids: set[str] = set()
    covered_titles: list[str] = []
    recent_papers: list[dict] = []
    for offset in range(1, days_back + 1):
        d = today - timedelta(days=offset)
        ds = d.isoformat()
        cache_path = Path(f"data/cache/daily-{ds}.json")
        if not cache_path.exists():
            continue
        try:
            data = json.loads(cache_path.read_text())
            for p in data.get("selected", []):
                pid = (p.get("id") or "").strip()
                title = (p.get("title") or "").strip()
                topic = (p.get("_topic_label") or p.get("_topic") or "").strip()
                summary = (p.get("summary") or "").strip()
                if pid:
                    covered_ids.add(pid)
                if title:
                    covered_titles.append(title)
                    recent_papers.append({
                        "date": ds,
                        "id": pid,
                        "title": title,
                        "topic": topic,
                        "summary": summary[:700],
                    })
        except Exception:
            pass
    return covered_ids, covered_titles, recent_papers


def _read_open_questions(max_chars: int = 3000) -> str:
    """Read the persistent open-questions tracker, truncated to max_chars."""
    oq_path = Path("data/open_questions.md")
    if not oq_path.exists():
        return ""
    text = oq_path.read_text().strip()
    if len(text) > max_chars:
        # Keep the most recent entries (end of file)
        text = "…[earlier entries truncated]…\n\n" + text[-max_chars:]
    return text


def _recent_papers_block(recent_papers: list[dict], max_items: int = 24) -> str:
    if not recent_papers:
        return "(no recent selected-paper cache records found)"
    lines = []
    for p in recent_papers[-max_items:]:
        lines.append(
            f"- {p.get('date', '')} | {p.get('topic', '')} | {p.get('title', '')}\n"
            f"  Abstract excerpt: {p.get('summary', '')}"
        )
    return "\n".join(lines)


def _default_selection_brief(
    open_questions: str,
    covered_titles: list[str],
) -> dict:
    return {
        "priority_signals": [
            "clear new result or constraint",
            "surprising implication or tension with recent work",
            "new method, data set, simulation, or measurement with reusable value",
            "paper that directly addresses a recurring open question",
        ],
        "open_questions_to_watch": [
            line.strip("- ").strip()
            for line in open_questions.splitlines()
            if line.strip().startswith("-")
        ][:8],
        "recently_covered_titles": covered_titles[-12:],
        "deprioritize": [
            "generic incremental applications unless the abstract states a concrete new finding",
            "papers already featured recently unless they add a distinct development",
        ],
        "selection_advice": (
            "Choose papers because they say something important or interesting, "
            "not because they fill a topic quota."
        ),
    }


def _build_selection_brief(
    history_block: str,
    open_questions: str,
    covered_titles: list[str],
    recent_papers: list[dict],
) -> dict:
    fallback = _default_selection_brief(open_questions, covered_titles)
    recent_block = _recent_papers_block(recent_papers)
    oq_block = open_questions or "(no accumulated open questions found)"

    prompt = f"""{COPERNICUS.prompt_preamble()}

You advise the discovery agent before it selects today's papers.

The final report should be a concise summary of the important new findings and
the most interesting points, not a broad catalog. Your job is to tell the
discovery agent what kinds of papers are worth selecting today.

Return ONLY valid JSON with this shape:
{{
  "priority_signals": ["signal to boost", "..."],
  "open_questions_to_watch": ["specific unresolved question", "..."],
  "recently_saturated_topics": ["topic or method already covered heavily", "..."],
  "deprioritize": ["pattern to avoid", "..."],
  "selection_advice": "2-4 sentences of concrete guidance for today's selection"
}}

Base your advice only on the recent briefing history and selected-paper cache below.
Prefer concrete scientific signals over generic topic labels.

Past briefing synthesis:
{history_block}

Accumulated open questions:
{oq_block}

Recently selected papers:
{recent_block}
"""

    try:
        text = gemini_generate(prompt=prompt, timeout=120.0)
        parsed = _extract_json(text)
        if isinstance(parsed, dict):
            return {**fallback, **parsed}
    except Exception:
        pass

    try:
        r = httpx.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
            timeout=300.0,
        )
        if r.status_code >= 400:
            r = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=300.0,
            )
        r.raise_for_status()
        parsed = _extract_json(r.json().get("response", ""))
        if isinstance(parsed, dict):
            return {**fallback, **parsed}
    except Exception:
        pass

    return fallback


def build_research_context(
    days_back: int = 7,
    today: date | None = None,
) -> dict:
    """
    Read the last `days_back` daily reports and caches, then ask Gemini to
    synthesize a concise context block for the upcoming digest.

    Returns:
        summary      : str        Markdown "## Recent context" section (may be empty)
        selection_brief: dict     Structured guidance for the selection agent
        covered_ids  : set[str]   arXiv IDs featured in recent briefings
        covered_titles: list[str] Titles of recently featured papers
    """
    if today is None:
        today = date.today()

    covered_ids, covered_titles, recent_papers = _read_recent_selected_papers(days_back, today)
    past_digests = _read_past_digests(days_back, today)
    open_questions = _read_open_questions()

    if not past_digests and not open_questions:
        return {
            "summary": "",
            "selection_brief": _default_selection_brief(open_questions, covered_titles),
            "covered_ids": covered_ids,
            "covered_titles": covered_titles,
        }

    history_block = "\n\n---\n\n".join(
        f"**{ds}**\n\n{text[:2500]}" for ds, text in past_digests
    ) if past_digests else "(no recent briefings found)"

    oq_block = (
        f"\n\n### Accumulated open questions (from all past briefings)\n\n{open_questions}"
        if open_questions else ""
    )

    prompt = f"""{COPERNICUS.prompt_preamble()}

You are a scientific context advisor for a daily astrophysics research briefing
covering galaxy clusters, galaxies, gravitational lensing, and dark matter.

Below are synthesized digests from the past {len(past_digests)} daily briefings (oldest first),
followed by a running log of open questions flagged across all previous runs.

Your task is to extract *durable context* that will help today's reader immediately understand
the current state of play in the field.

Write a **concise Markdown section** (250–400 words) headed exactly:

## Recent context and open threads

Focus on:
- Themes and methods that have appeared in multiple briefings
- Active debates or tensions that keep resurfacing
- Open questions that have been flagged repeatedly and remain unresolved
- Any notable shifts or momentum in specific sub-areas

Do NOT list individual papers or dates. Write in present tense as though briefing a colleague.
Be concise and specific — no filler sentences.

Past briefings (oldest first):

{history_block}{oq_block}
"""

    summary = ""
    try:
        summary = gemini_generate(prompt=prompt, timeout=120.0)
    except Exception:
        try:
            r = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=300.0,
            )
            r.raise_for_status()
            summary = r.json().get("response", "").strip()
        except Exception:
            # Graceful degradation: use a plain bullet summary of recent open questions
            lines = []
            for ds, text in past_digests[-3:]:
                m = re.search(r"## (?:Open questions|Future Directions)(.*?)(?=\n## |\Z)", text, flags=re.DOTALL | re.IGNORECASE)
                if m:
                    lines.append(m.group(1).strip()[:400])
            if lines:
                summary = "## Recent context and open threads\n\n" + "\n\n".join(lines)

    selection_brief = _build_selection_brief(
        history_block=history_block,
        open_questions=open_questions,
        covered_titles=covered_titles,
        recent_papers=recent_papers,
    )

    return {
        "summary": summary,
        "selection_brief": selection_brief,
        "covered_ids": covered_ids,
        "covered_titles": covered_titles,
    }
