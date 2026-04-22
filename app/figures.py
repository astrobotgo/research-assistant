import hashlib
import json
import re
from base64 import b64encode
from pathlib import Path

import fitz
import httpx


def _safe_name(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip())
    return cleaned.strip("-")[:120] or "paper"


def _paper_key(paper: dict) -> str:
    pid = paper.get("id") or paper.get("title", "paper")
    return _safe_name(pid)


def _download_pdf(pdf_url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    r = httpx.get(pdf_url, timeout=120.0, follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)


def _extract_caption(page: fitz.Page, img_rect: fitz.Rect, max_chars: int = 300) -> str:
    """
    Extract figure caption text from the region immediately below (or to the
    side of) the image on the same page. Returns empty string if nothing found.
    """
    # Search in a band below the image, up to 120 points tall
    search_rect = fitz.Rect(
        img_rect.x0,
        img_rect.y1,
        img_rect.x1,
        img_rect.y1 + 120,
    )
    # Also search a narrow band above (some papers put captions above)
    search_rect_above = fitz.Rect(
        img_rect.x0,
        max(0, img_rect.y0 - 80),
        img_rect.x1,
        img_rect.y0,
    )

    caption = ""
    for rect in (search_rect, search_rect_above):
        blocks = page.get_text("blocks", clip=rect)
        for block in blocks:
            text = block[4].strip() if len(block) > 4 else ""
            # Heuristic: captions typically start with "Fig" or "Figure" or a number
            if re.match(r"^(fig|figure|panel|\d+\.)", text, flags=re.IGNORECASE):
                caption = text[:max_chars]
                break
        if caption:
            break

    # Fallback: grab any text in the below-band even without the "Fig" marker
    if not caption:
        blocks = page.get_text("blocks", clip=search_rect)
        texts = [b[4].strip() for b in blocks if len(b) > 4 and b[4].strip()]
        if texts:
            caption = " ".join(texts)[:max_chars]

    return caption


def _render_page_image(page: fitz.Page, xref: int, zoom: float = 2.0) -> tuple[bytes | None, fitz.Rect | None]:
    """
    Render an embedded image as it appears on the page.

    Returns (png_bytes, image_rect_on_page) so callers can extract captions.
    Using the raw extracted image stream can lose the PDF placement transform,
    which causes some figures to appear upside down or rotated in the digest PDF.
    """
    try:
        rects = page.get_image_rects(xref)
    except Exception:
        return None, None
    if not rects:
        return None, None

    rect = max(rects, key=lambda r: r.width * r.height)
    if rect.width <= 0 or rect.height <= 0:
        return None, None

    try:
        pix = page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom),
            clip=rect,
            alpha=False,
        )
    except Exception:
        return None, None
    return pix.tobytes("png"), rect


def _rank_with_gemini(
    paper: dict,
    candidates: list[tuple[int, int, bytes, str]],  # (area, page_num, blob, caption)
    max_figures: int,
) -> list[dict]:
    api_key = (__import__("os").environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key or not candidates:
        return []

    model = (__import__("os").environ.get("GEMINI_MODEL") or "gemini-1.5-flash").strip()
    shortlisted = candidates[: min(8, len(candidates))]

    parts: list[dict] = [
        {
            "text": (
                "You are ranking research paper figures by scientific usefulness for a daily digest. "
                "Prefer plots/diagrams/results over logos/decorative images. "
                "Return strict JSON only with this shape: "
                "{\"keep\":[{\"index\":1,\"reason\":\"...\"}]} "
                "where indices are from the labels below and reason is one or two concise sentences "
                "explaining why this figure is especially important for understanding the paper."
            )
        },
        {"text": f"Paper title: {paper.get('title', '')}"},
        {"text": f"Paper abstract: {(paper.get('summary') or '')[:3000]}"},
    ]
    for i, (area, page_num, blob, caption) in enumerate(shortlisted, start=1):
        caption_note = f" | Caption: {caption}" if caption else ""
        parts.append({"text": f"Figure {i} (page {page_num}, area {area}{caption_note}):"})
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/png",
                    "data": b64encode(blob).decode("ascii"),
                }
            }
        )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    payload = {"contents": [{"role": "user", "parts": parts}]}
    r = httpx.post(url, json=payload, timeout=90.0)
    r.raise_for_status()
    data = r.json()
    text = ""
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if part.get("text"):
                text += part["text"]
    text = text.strip()
    if not text:
        return []

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = text.replace("```", "").strip()
    try:
        parsed = json.loads(text)
        keep = parsed.get("keep") or []
    except Exception:
        keep = []

    out: list[dict] = []
    for k in keep:
        if isinstance(k, int):
            idx = k
            reason = ""
        elif isinstance(k, dict):
            idx = k.get("index")
            reason = str(k.get("reason", "")).strip()
        else:
            continue
        if isinstance(idx, int) and 1 <= idx <= len(shortlisted):
            area, page_num, blob, caption = shortlisted[idx - 1]
            out.append(
                {
                    "area": area,
                    "page": page_num,
                    "blob": blob,
                    "reason": reason,
                    "caption": caption,
                }
            )
        if len(out) >= max_figures:
            break
    return out


def extract_key_figures(
    paper: dict,
    cache_pdf_dir: Path,
    cache_fig_dir: Path,
    max_figures: int = 2,
    use_gemini: bool = False,
) -> list[dict]:
    pdf_url = paper.get("pdf_url")
    if not pdf_url:
        return []

    key = _paper_key(paper)
    pdf_path = cache_pdf_dir / f"{key}.pdf"
    fig_dir = cache_fig_dir / key
    fig_dir.mkdir(parents=True, exist_ok=True)

    _download_pdf(pdf_url, pdf_path)

    doc = fitz.open(pdf_path)
    # candidates: (area, page_num, blob, caption)
    candidates: list[tuple[int, int, bytes, str]] = []
    seen_hashes: set[str] = set()

    max_pages = min(len(doc), 20)
    for page_idx in range(max_pages):
        page = doc[page_idx]
        for img in page.get_images(full=True):
            xref = img[0]
            try:
                img_dict = doc.extract_image(xref)
            except Exception:
                continue
            blob = img_dict.get("image")
            if not blob:
                continue
            digest = hashlib.sha1(blob).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)

            rendered, img_rect = _render_page_image(page, xref)
            if rendered:
                blob = rendered
                caption = _extract_caption(page, img_rect) if img_rect else ""
                try:
                    width = int(img_rect.width * 2)
                    height = int(img_rect.height * 2)
                except Exception:
                    width = int(img_dict.get("width") or 0)
                    height = int(img_dict.get("height") or 0)
            else:
                caption = ""
                width = int(img_dict.get("width") or 0)
                height = int(img_dict.get("height") or 0)

            area = width * height
            if area < 80000:
                continue
            candidates.append((area, page_idx + 1, blob, caption))

    doc.close()

    candidates.sort(key=lambda x: x[0], reverse=True)

    ranked = [
        {"area": area, "page": page_num, "blob": blob, "reason": "", "caption": caption}
        for area, page_num, blob, caption in candidates[:max_figures]
    ]
    if use_gemini:
        try:
            gemini_ranked = _rank_with_gemini(paper, candidates, max_figures=max_figures)
            if gemini_ranked:
                ranked = gemini_ranked
        except Exception:
            pass

    saved: list[dict] = []
    for i, item in enumerate(ranked[:max_figures], start=1):
        blob = item["blob"]
        out = fig_dir / f"figure_{i}.png"
        out.write_bytes(blob)
        saved.append(
            {
                "path": str(out),
                "reason": item.get("reason", ""),
                "caption": item.get("caption", ""),
                "page": item.get("page"),
            }
        )
    return saved
