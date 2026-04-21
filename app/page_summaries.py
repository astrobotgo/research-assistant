from pathlib import Path

import fitz
import httpx

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
    Return one concise summary per source PDF page.
    If max_pages > 0, only summarize up to that many pages.
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
    out: list[dict] = []

    for page_idx in range(limit):
        page = doc[page_idx]
        text = (page.get_text("text") or "").strip()
        if not text:
            out.append(
                {
                    "page": page_idx + 1,
                    "summary": "No extractable text on this page.",
                }
            )
            continue

        clipped = text[:5000]
        prompt = f"""You are summarizing a research paper page for expert readers.
Write 2-4 concise sentences that capture the core point of this page.
Do not invent details beyond the provided page text.

Paper title: {paper.get("title", "")}
Page number: {page_idx + 1}

Page text:
{clipped}
"""
        try:
            r = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=180.0,
            )
            r.raise_for_status()
            summary = r.json().get("response", "").strip()
            if not summary:
                summary = clipped[:400]
        except Exception:
            summary = clipped[:400]

        out.append({"page": page_idx + 1, "summary": summary})

    doc.close()
    return out

