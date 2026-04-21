import json
import re

import httpx

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


def _catalog_for_prompt(papers: list[dict]) -> str:
    lines = []
    for i, p in enumerate(papers, start=1):
        title = p.get("title", "")
        topic = p.get("_topic_label", "")
        abstract = (p.get("summary") or "")[:1800]
        cats = ", ".join(p.get("categories", []) or [])
        lines.append(
            f"[{i}] Topic: {topic}\n"
            f"    Title: {title}\n"
            f"    Categories: {cats}\n"
            f"    Abstract: {abstract}\n"
        )
    return "\n".join(lines)


def select_top_papers(
    papers: list[dict],
    k: int = 10,
    timeout: float = 360.0,
) -> tuple[list[dict], str]:
    """
    Ask the local LLM to pick up to k papers from the pool.
    Returns (selected_papers_in_order, rationale_or_error_note).
    """
    if not papers or k <= 0:
        return [], ""
    if len(papers) <= k:
        return list(papers), "Pool smaller than target; including all."

    catalog = _catalog_for_prompt(papers)
    prompt = f"""You curate a daily astrophysics briefing covering:
galaxy clusters, galaxies, gravitational lensing, and dark matter.

From the numbered candidates below, choose exactly {k} papers to present.
Prioritize: scientific substance, novelty, and clarity of contribution.
Aim for breadth across the four themes when the abstracts support it; do not
pick {k} papers all from one theme if strong options exist elsewhere.

Return ONLY valid JSON with this shape (no markdown fences):
{{"indices":[1,3,7,...],"brief_reason":"one short sentence"}}

Use each index exactly once. Indices refer to [n] in the list. There are {len(papers)} candidates.

CANDIDATES:
{catalog}
"""

    try:
        text = gemini_generate(prompt=prompt, timeout=timeout)
        parsed = _extract_json(text)
        indices: list = []
        reason = ""
        if isinstance(parsed, dict):
            indices = parsed.get("indices") or []
            reason = str(parsed.get("brief_reason", "")).strip()
        if not indices:
            indices, reason_fb = _fallback_parse_indices(text)
            if not reason:
                reason = reason_fb
        if not indices:
            raise RuntimeError("Gemini JSON parse failed")
        seen: set[int] = set()
        chosen: list[dict] = []
        for raw in indices:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if idx < 1 or idx > len(papers) or idx in seen:
                continue
            seen.add(idx)
            chosen.append(papers[idx - 1])
            if len(chosen) >= k:
                break
        if len(chosen) < k:
            for p in papers:
                if p in chosen:
                    continue
                chosen.append(p)
                if len(chosen) >= k:
                    break
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
        parsed = _extract_json(text)
        indices: list = []
        reason = ""
        if isinstance(parsed, dict):
            indices = parsed.get("indices") or []
            reason = str(parsed.get("brief_reason", "")).strip()
        if not indices:
            indices, reason_fb = _fallback_parse_indices(text)
            if not reason:
                reason = reason_fb
        if not indices:
            return papers[:k], "_Selection JSON parse failed; using newest k._"
        seen: set[int] = set()
        chosen: list[dict] = []
        for raw in indices:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if idx < 1 or idx > len(papers) or idx in seen:
                continue
            seen.add(idx)
            chosen.append(papers[idx - 1])
            if len(chosen) >= k:
                break

        if len(chosen) < k:
            for i, p in enumerate(papers):
                if p in chosen:
                    continue
                chosen.append(p)
                if len(chosen) >= k:
                    break

        note = reason or "Ollama-selected subset."
        return chosen, note
    except Exception as e:
        return papers[:k], f"_Selection failed ({e}); using newest k._"
