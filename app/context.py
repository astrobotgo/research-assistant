"""
Context agent: maintains a persistent "state of the field" document that
accumulates knowledge over months, plus short-term paper deduplication.

field_state.md is a living ~1500-word document Copernicus rewrites each day,
compressing new findings into durable long-term context spanning the full
history of the assistant's runs.
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


FIELD_STATE_PATH = Path("data/field_state.md")


def read_field_state() -> str:
    """Return the current state-of-the-field document, or empty string if none exists yet."""
    if not FIELD_STATE_PATH.exists():
        return ""
    return FIELD_STATE_PATH.read_text().strip()


def update_field_state(today: str, digest_md: str) -> None:
    """
    Ask Copernicus to merge today's digest into the persistent field state document.
    The document is rewritten in place, keeping it to ~1500 words.
    Astronomy moves slowly — the field state accumulates knowledge over months.
    """
    current_state = read_field_state()

    current_block = (
        f"Current state of the field document (maintain and update this):\n\n{current_state}"
        if current_state
        else "No prior field state exists yet. Write the initial version from today's digest."
    )

    prompt = f"""{COPERNICUS.prompt_preamble()}

You maintain a persistent "state of the field" document for astrophysics research
covering galaxy clusters, galaxies, gravitational lensing, and dark matter.

This document is your long-term scientific memory. It is updated after every daily
briefing and accumulates knowledge over months. Astronomy progresses slowly —
findings, debates, and open questions persist and evolve over months to years.

Your task: given the current field state document and today's new digest, rewrite
the field state document to incorporate new findings, update ongoing debates, retire
resolved questions, and track new open problems.

Rules:
- Keep the document to 1200–1800 words.
- Write in present tense as a scientific summary, not a log of events.
- Do not mention specific dates or "today's papers" — fold findings into the narrative.
- Preserve important ongoing debates and unresolved tensions even if not in today's digest.
- Retire findings that have been superseded or resolved.
- Use specific numbers, constraints, and method names when available.
- Structure the document with these sections (use only those that have content):

## Active research fronts
What problems are actively being worked on and why they matter.

## Established recent findings
Concrete results, constraints, and measurements that have emerged recently and appear durable.

## Ongoing debates and tensions
Where the field disagrees, what the competing interpretations are, and what evidence exists on each side.

## Open questions
Specific unresolved scientific questions the field is actively trying to answer.

## Methods and data gaining traction
Techniques, surveys, simulations, or instruments that are producing new results.

---

{current_block}

---

Today's new digest ({today}):

{digest_md[:6000]}
"""

    new_state = ""
    try:
        new_state = gemini_generate(prompt=prompt, timeout=180.0).strip()
    except Exception:
        try:
            r = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=300.0,
            )
            r.raise_for_status()
            new_state = r.json().get("response", "").strip()
        except Exception:
            return

    if new_state:
        FIELD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIELD_STATE_PATH.write_text(new_state)


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
    days_back: int = 30,
    today: date | None = None,
) -> dict:
    """
    Build context for today's digest using the persistent field state document
    as long-term memory, plus recent paper cache for deduplication.

    The field_state.md document accumulates months of scientific knowledge.
    days_back controls only the deduplication window (how far back to look for
    already-covered paper IDs).

    Returns:
        summary        : str        The field state document (long-term context)
        selection_brief: dict       Structured guidance for the selection agent
        covered_ids    : set[str]   arXiv IDs featured recently (dedup window)
        covered_titles : list[str]  Titles of recently featured papers
    """
    if today is None:
        today = date.today()

    covered_ids, covered_titles, recent_papers = _read_recent_selected_papers(days_back, today)
    field_state = read_field_state()
    open_questions = _read_open_questions()

    # Build selection brief from field state + recent papers
    history_block = field_state if field_state else "(no field state document yet)"
    selection_brief = _build_selection_brief(
        history_block=history_block,
        open_questions=open_questions,
        covered_titles=covered_titles,
        recent_papers=recent_papers,
    )

    return {
        "summary": field_state,
        "selection_brief": selection_brief,
        "covered_ids": covered_ids,
        "covered_titles": covered_titles,
    }


def build_historical_backdrop(papers: list[dict]) -> str:
    """
    Place today's selected papers in the 20-year arc of the field.
    Called after enrichment so it receives the actual selected papers.
    Returns a Markdown string headed '## Historical backdrop', or '' on failure.
    """
    if not papers:
        return ""

    paper_items = []
    for p in papers:
        title = (p.get("title") or "").strip()
        abstract = (p.get("summary") or "")[:350].strip()
        if title:
            paper_items.append(f"- **{title}**: {abstract}")
    if not paper_items:
        return ""
    papers_block = "\n".join(paper_items)

    prompt = f"""You are Copernicus, a scientific advisor who specialises in the history and long-term
evolution of astrophysics research covering galaxy clusters, galaxies, gravitational lensing,
and dark matter — roughly the past 20 years (2005–2025).

Today's briefing highlights these papers:

{papers_block}

Write a **concise Markdown section** (300–450 words) headed exactly:

## Historical backdrop

For each major theme you see in today's papers, identify:
- The landmark results, surveys, or theoretical frameworks that established the foundation
  (name them explicitly: Bullet Cluster, SDSS, Planck, DES, HST/JWST, SPT, ACT, eROSITA,
  CLASH, Frontier Fields, XMM-Newton, Chandra, DESI, Euclid, Rubin/LSST, etc.)
- How understanding in that sub-area has evolved over the past two decades
- Active long-running tensions or open problems that today's papers push on

Be specific and name real results. Write in present tense as if briefing an expert who knows the
field well but needs historical grounding relative to today's papers. No filler sentences.
"""

    backdrop = ""
    try:
        backdrop = gemini_generate(prompt=prompt, timeout=120.0)
    except Exception:
        try:
            r = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=300.0,
            )
            r.raise_for_status()
            backdrop = r.json().get("response", "").strip()
        except Exception:
            pass

    return backdrop
