from pathlib import Path

import fitz
import httpx

from app.gemini_llm import gemini_generate
from app.summarize import OLLAMA_HOST, OLLAMA_MODEL


def _download_pdf(pdf_url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    r = httpx.get(pdf_url, timeout=120.0, follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)


def summarize_pdf_pages(
    paper: dict,
    cache_pdf_dir: Path,
    max_pages: int = 0,
) -> list[dict]:
    """
    Extract text from all pages and summarize the full paper in a single LLM call.
    Returns a list of dicts with 'page' and 'summary' keys, one entry per section
    the LLM identifies (introduction, methods, results, conclusions, etc.).
    If max_pages > 0, only the first max_pages pages are included.
    """
    pdf_url = paper.get("pdf_url")
    if not pdf_url:
        return []

    paper_id = (paper.get("id") or paper.get("title") or "paper").replace("/", "_")
    pdf_path = cache_pdf_dir / f"{paper_id}.pdf"
    _download_pdf(pdf_url, pdf_path)

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    limit = total_pages if max_pages <= 0 else min(total_pages, max_pages)

    # Collect all page text up to a total character budget
    CHAR_BUDGET = 40_000
    pages_text: list[tuple[int, str]] = []
    total_chars = 0
    for page_idx in range(limit):
        text = (doc[page_idx].get_text("text") or "").strip()
        if not text:
            continue
        remaining = CHAR_BUDGET - total_chars
        if remaining <= 0:
            break
        clipped = text[:remaining]
        pages_text.append((page_idx + 1, clipped))
        total_chars += len(clipped)

    doc.close()

    if not pages_text:
        return []

    full_text = "\n\n".join(f"=== Page {pg} ===\n{txt}" for pg, txt in pages_text)

    prompt = f"""You are an expert astrophysicist summarizing a research paper for colleagues.

Read the full paper text below and write a structured summary covering:
1. What question or problem the paper addresses
2. Data, instruments, or simulations used
3. Key methods and analysis approach
4. Specific results with numbers (measurements, constraints, significance levels)
5. Conclusions and implications for the field
6. Any caveats, limitations, or open questions raised

Be specific and quantitative where the paper provides numbers. Do not pad with vague sentences.
Write 8-15 sentences total as flowing prose, not bullet points.

Paper title: {paper.get("title", "")}

Paper text:
{full_text}
"""

    summary = ""
    try:
        summary = gemini_generate(prompt=prompt, timeout=240.0).strip()
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
            pass

    if not summary:
        # Fallback: return raw text excerpts as pseudo-summaries
        return [{"page": pg, "summary": txt[:400]} for pg, txt in pages_text[:3]]

    return [{"page": 1, "summary": summary}]
