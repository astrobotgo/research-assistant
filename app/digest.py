import httpx

from app.agents import COPERNICUS
from app.gemini_llm import gemini_generate
from app.summarize import OLLAMA_HOST, OLLAMA_MODEL


def _paper_brief(p: dict, page_summaries: list[dict] | None = None) -> str:
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
    if p.get("_selection_reason"):
        lines.append(f"  - Why selected: {p['_selection_reason']}")
    lines.append(f"  - Abstract: {excerpt}")
    background = p.get("background") or {}
    if isinstance(background, str):
        background = {"text": background, "verified": []}
    bg_text = background.get("text", "").strip()[:600]
    if bg_text:
        lines.append(f"  - Prior literature context: {bg_text}")
    verified_papers = background.get("verified") or []
    if verified_papers:
        lines.append("  - Key prior papers:")
        for vp in verified_papers:
            vt = vp.get("title", "")
            vy = vp.get("year", "")
            vurl = vp.get("arxiv_url", "")
            vreason = vp.get("reason", "")
            entry = f"    - [{vt} ({vy})]({vurl})" if vurl else f"    - {vt} ({vy})"
            if vreason:
                entry += f" — {vreason}"
            lines.append(entry)
    analysis = p.get("analysis") or {}
    if isinstance(analysis, dict) and analysis.get("one_sentence_summary"):
        lines.append(f"  - Prior one-line take: {analysis['one_sentence_summary']}")
    s2 = p.get("semantic_scholar")
    if isinstance(s2, dict) and s2.get("citationCount") is not None:
        lines.append(
            f"  - Citations (Semantic Scholar): {s2.get('citationCount', '')}"
        )
    if page_summaries:
        lines.append("  - Full paper content:")
        for ps in page_summaries:
            lines.append(f"    {ps['summary'][:2000]}")
    return "\n".join(lines)


def synthesize_research_digest(
    papers: list[dict],
    context: str = "",
    page_summary_map: dict[str, list[dict]] | None = None,
) -> str:
    if not papers:
        return "_No papers matched the scan criteria for this period._"

    def _brief(p: dict) -> str:
        pid = p.get("id") or p.get("title") or ""
        page_sums = (page_summary_map or {}).get(pid, [])
        return _paper_brief(p, page_summaries=page_sums or None)

    catalog = "\n\n".join(_brief(p) for p in papers)

    context_block = ""
    if context:
        context_block = (
            f"{context}\n\n"
            "Use the above context to identify which submissions are genuinely new, "
            "which extend recent threads, and which answer or sharpen unresolved "
            "questions. Do not spend space recapping background unless it explains "
            "why a new finding matters.\n\n"
            "---\n\n"
        )

    prompt = f"""{COPERNICUS.prompt_preamble()}

You are writing an internal research briefing as an expert astrophysicist and cosmologist.

You are given recent arXiv preprints spanning galaxy clusters, galaxies,
gravitational lensing, and dark matter. Write a **findings-first Markdown
briefing** for researchers who already know the field.

Rules:
- Where full paper content (page summaries) is provided, use it — go beyond the abstract to extract specific numbers, constraints, figures, methods, and conclusions from the body of the paper.
- Each paper includes a "Prior literature context" paragraph drawn from the broader scientific literature. Use this to explain why a finding matters, how it compares to established results, and where it fits in the progression of the field.
- For specific new results reported in today's papers, ground claims in the supplied text. For background and context, draw freely on your knowledge of the astrophysics literature — key surveys, established constraints, foundational debates, and prior measurements.
- Lead with important new findings and the most interesting points. A paper should earn space because it says something notable, not because it belongs to a topic bucket.
- Extract concrete results: numbers, confidence levels, dataset names, tensions with prior work, methodological advances.
- Explicitly connect new results to prior work: does this confirm, challenge, or refine established results? How large is the improvement over previous measurements?
- Mention topic balance only when it helps the reader understand the day's strongest papers.
- If a paper is incremental or its content is unclear, say so briefly and do not inflate its importance.
- Prefer accurate, cautious language over hype.

{context_block}Use this structure:

## What is new and worth noticing
4-7 sentences summarizing the most important findings, sharpest constraints,
surprising implications, or methodological advances across today's selected papers.

## Most interesting papers
Bullet list of up to 8 papers. For each: title plus 1-2 sentences explaining the
important new finding or interesting point, and why a researcher should care.

## Findings by theme
Short subsections only for themes that have meaningful findings today. Synthesize
results, methods, and tensions; do not force all four scanner topics to appear.

## Connections and tensions
How today's findings relate to recent open questions, recurring debates, or each other.

## Open questions and follow-ups
What today's abstracts suggest is unresolved or worth tracking next.

---

Preprints (with topic focus labels from the scanner):

{catalog}
"""

    try:
        return gemini_generate(prompt=prompt, timeout=600.0)
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
