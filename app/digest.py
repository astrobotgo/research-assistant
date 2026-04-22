import httpx

from app.gemini_llm import gemini_generate
from app.summarize import OLLAMA_HOST, OLLAMA_MODEL


def _paper_brief(p: dict) -> str:
    title = p.get("title", "")
    published = p.get("published", "")
    topic = p.get("_topic_label", p.get("_topic", ""))
    summary = (p.get("summary") or "").strip()
    excerpt = summary[:1200] + ("…" if len(summary) > 1200 else "")
    pdf = p.get("pdf_url") or ""
    lines = [
        f"- **{title}**",
        f"  - Topic focus: {topic}",
        f"  - Published: {published}",
    ]
    if pdf:
        lines.append(f"  - PDF: {pdf}")
    lines.append(f"  - Abstract (excerpt): {excerpt}")
    analysis = p.get("analysis") or {}
    if isinstance(analysis, dict) and analysis.get("one_sentence_summary"):
        lines.append(f"  - Prior one-line take: {analysis['one_sentence_summary']}")
    s2 = p.get("semantic_scholar")
    if isinstance(s2, dict) and s2.get("citationCount") is not None:
        lines.append(
            f"  - Citations (Semantic Scholar): {s2.get('citationCount', '')}"
        )
    return "\n".join(lines)


def synthesize_research_digest(papers: list[dict], context: str = "") -> str:
    if not papers:
        return "_No papers matched the scan criteria for this period._"

    catalog = "\n\n".join(_paper_brief(p) for p in papers)

    context_block = ""
    if context:
        context_block = (
            f"{context}\n\n"
            "Use the above context to:\n"
            "- Note when today's papers resolve, extend, or contradict recent open questions.\n"
            "- Flag if a theme is newly appearing vs. a continuing trend.\n"
            "- Avoid re-summarizing background that was covered in the past week unless today's papers add something new.\n\n"
            "---\n\n"
        )

    prompt = f"""You are an expert astrophysicist and cosmologist writing an internal research briefing.

You are given recent arXiv preprints spanning galaxy clusters, galaxies, gravitational lensing, and dark matter.
Write a **single cohesive Markdown document** for researchers who already know the field.

Rules:
- Ground every claim in the supplied abstracts/snippets; do not invent empirical results or citations not implied by the text.
- If an area has few or no papers in the list, say so briefly rather than speculating.
- Prefer accurate, cautious language over hype.

{context_block}Use this structure:

## Executive overview
3–6 sentences on what is newly appearing across these submissions.

## Galaxy clusters
## Galaxies
## Gravitational lensing
## Dark matter

(For each section above: synthesize themes, methods, and tensions **only** from papers tagged with that topic focus, and note cross-connections where obvious.)

## Cross-cutting themes
Patterns that span multiple areas.

## Papers to read first
Bullet list of up to 8 entries: title + one sentence on why it matters (only from this set).

## Open questions and follow-ups
What the abstracts suggest is unresolved or worth tracking.

---

Preprints (with topic focus labels from the scanner):

{catalog}
"""

    try:
        return gemini_generate(prompt=prompt, timeout=300.0)
    except Exception:
        r = httpx.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=600.0,
        )
        r.raise_for_status()
        return r.json()["response"].strip()
