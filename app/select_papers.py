import json
import re

import httpx

from app.agents import PTOLEMY
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
        chunk = text[start : end + 1]
        try:
            return json.loads(chunk)
        except Exception:
            pass
    return None


def _fallback_parse_indices(text: str) -> tuple[list[int], str]:
    """If JSON is noisy, still try to recover indices and optional reason."""
    indices: list[int] = []
    m = re.search(r'"indices"\s*:\s*\[([^\]]*)\]', text, flags=re.IGNORECASE)
    if m:
        for part in m.group(1).replace("\n", " ").split(","):
            part = part.strip()
            if part.isdigit():
                indices.append(int(part))
    reason_m = re.search(r'"brief_reason"\s*:\s*"([^"]*)"', text, flags=re.DOTALL)
    reason = reason_m.group(1).strip() if reason_m else ""
    return indices, reason


def _selection_context_for_prompt(selection_context: dict | str | None) -> str:
    if not selection_context:
        return ""
    if isinstance(selection_context, str):
        body = selection_context.strip()
    else:
        body = json.dumps(selection_context, indent=2)
    if not body:
        return ""
    return (
        "\nCopernicus selection brief:\n"
        f"{body}\n\n"
        "Use this brief as scientific context, but still judge each candidate "
        "by the evidence in its title and abstract.\n"
    )


def _parse_selection(text: str) -> tuple[list[int], str, dict[int, str]]:
    parsed = _extract_json(text)
    reason = ""
    reason_by_index: dict[int, str] = {}
    indices: list[int] = []

    if isinstance(parsed, dict):
        reason = str(parsed.get("brief_reason", "")).strip()
        picks = parsed.get("picks")
        if isinstance(picks, list):
            for pick in picks:
                if not isinstance(pick, dict):
                    continue
                try:
                    idx = int(pick.get("index"))
                except (TypeError, ValueError):
                    continue
                if idx <= 0:
                    continue
                indices.append(idx)
                pick_reason = str(pick.get("reason", "")).strip()
                if pick_reason:
                    reason_by_index[idx] = pick_reason
        if not indices:
            for raw in parsed.get("indices") or []:
                try:
                    indices.append(int(raw))
                except (TypeError, ValueError):
                    continue

    if not indices:
        indices, fallback_reason = _fallback_parse_indices(text)
        if not reason:
            reason = fallback_reason

    return indices, reason, reason_by_index


def _chosen_from_indices(
    papers: list[dict],
    indices: list[int],
    k: int,
    reason_by_index: dict[int, str],
) -> list[dict]:
    seen_indices: set[int] = set()
    seen_keys: set[str] = set()
    chosen: list[dict] = []
    for raw in indices:
        idx = int(raw)
        if idx < 1 or idx > len(papers) or idx in seen_indices:
            continue
        paper = dict(papers[idx - 1])
        key = paper.get("id") or paper.get("title") or str(idx)
        if key in seen_keys:
            continue
        pick_reason = reason_by_index.get(idx, "")
        if pick_reason:
            paper["_selection_reason"] = pick_reason
        seen_indices.add(idx)
        seen_keys.add(key)
        chosen.append(paper)
        if len(chosen) >= k:
            break
    if len(chosen) < k:
        for i, paper in enumerate(papers, start=1):
            key = paper.get("id") or paper.get("title") or str(i)
            if key in seen_keys:
                continue
            chosen.append(dict(paper))
            seen_keys.add(key)
            if len(chosen) >= k:
                break
    return chosen


def _catalog_for_prompt(papers: list[dict]) -> str:
    lines = []
    for i, p in enumerate(papers, start=1):
        title = p.get("title", "")
        topic = p.get("_topic_label", "")
        abstract = (p.get("summary") or "")[:1800]
        cats = ", ".join(p.get("categories", []) or [])
        score = p.get("_priority_score", 0)
        all_topics = p.get("_topic_matches") or [topic]
        topics_str = ", ".join(all_topics) if len(all_topics) > 1 else topic
        priority_note = f" [priority score: {score}]" if score > 0 else ""
        lines.append(
            f"[{i}] Topic: {topics_str}{priority_note}\n"
            f"    Title: {title}\n"
            f"    Categories: {cats}\n"
            f"    Abstract: {abstract}\n"
        )
    return "\n".join(lines)


def select_top_papers(
    papers: list[dict],
    k: int = 10,
    timeout: float = 360.0,
    covered_ids: set[str] | None = None,
    selection_context: dict | str | None = None,
) -> tuple[list[dict], str]:
    """
    Ask the LLM to pick up to k papers from the pool.
    Returns (selected_papers_in_order, rationale_or_error_note).

    covered_ids: arXiv IDs featured in recent briefings; the LLM is told to
    deprioritize these unless there is a compelling reason to revisit them.
    selection_context: Copernicus' structured advice about what is worth
    selecting today.
    """
    if not papers or k <= 0:
        return [], ""
    if len(papers) <= k:
        return list(papers), "Pool smaller than target; including all."

    catalog = _catalog_for_prompt(papers)
    context_note = _selection_context_for_prompt(selection_context)

    recently_seen_note = ""
    if covered_ids:
        repeat_indices = []
        for i, p in enumerate(papers, start=1):
            pid = (p.get("id") or "").strip()
            if pid and pid in covered_ids:
                repeat_indices.append(str(i))
        if repeat_indices:
            recently_seen_note = (
                f"\nNote: candidates at indices [{', '.join(repeat_indices)}] appeared in "
                "a recent daily briefing. Deprioritize them unless they have significant new "
                "developments not covered before.\n"
            )

    prompt = f"""{PTOLEMY.prompt_preamble()}

Your task is to curate a daily astrophysics briefing covering:
galaxy clusters, galaxies, gravitational lensing, and dark matter.

From the numbered candidates below, choose exactly {k} papers to present.
Prioritize papers whose abstracts contain important new findings, unusually
interesting points, clear constraints, surprising implications, notable data,
or methods that change how a problem can be attacked.
Papers marked with a [priority score] match multiple research topics or key
user-defined keywords — give them extra weight, all else being equal.
Breadth across themes is useful, but it is secondary: do not include a routine
paper just to fill a topic slot, and do not bury the most interesting paper
because its theme was already represented.
{recently_seen_note}
{context_note}
Return ONLY valid JSON with this shape (no markdown fences):
{{
  "picks": [
    {{"index": 1, "reason": "why this paper has an important new finding or interesting point"}}
  ],
  "brief_reason": "one short sentence summarizing the selection"
}}

Use each index exactly once. Indices refer to [n] in the list. There are {len(papers)} candidates.

CANDIDATES:
{catalog}
"""

    try:
        text = gemini_generate(prompt=prompt, timeout=timeout)
        indices, reason, reason_by_index = _parse_selection(text)
        if not indices:
            raise RuntimeError("Gemini JSON parse failed")
        chosen = _chosen_from_indices(papers, indices, k, reason_by_index)
        note = reason or "Gemini-selected subset."
        return chosen, note
    except Exception:
        pass

    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        r = httpx.post(
            f"{OLLAMA_HOST}/api/generate",
            json=payload,
            timeout=timeout,
        )
        if r.status_code >= 400:
            payload.pop("format", None)
            r = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json=payload,
                timeout=timeout,
            )
        r.raise_for_status()
        text = r.json().get("response", "").strip()
        indices, reason, reason_by_index = _parse_selection(text)
        if not indices:
            return papers[:k], "_Selection JSON parse failed; using newest k._"
        chosen = _chosen_from_indices(papers, indices, k, reason_by_index)
        note = reason or "Ollama-selected subset."
        return chosen, note
    except Exception as e:
        return papers[:k], f"_Selection failed ({e}); using newest k._"
