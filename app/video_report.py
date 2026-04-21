from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import fitz

from app.gemini_llm import gemini_generate

PIPER_BIN = os.getenv("PIPER_BIN", "piper")
PIPER_MODEL = os.getenv(
    "PIPER_MODEL",
    str(Path("models/piper/en_US-amy-medium.onnx")),
)


def _render_pdf_pages(pdf_path: Path, page_dir: Path) -> list[Path]:
    doc = fitz.open(pdf_path)
    image_paths: list[Path] = []
    try:
        for idx, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            out_path = page_dir / f"page-{idx + 1:03d}.png"
            pix.save(out_path)
            image_paths.append(out_path)
    finally:
        doc.close()
    return image_paths


def _extract_page_texts(pdf_path: Path, max_chars: int = 5000) -> list[str]:
    doc = fitz.open(pdf_path)
    texts: list[str] = []
    try:
        for page in doc:
            text = " ".join((page.get_text("text") or "").split())
            texts.append(text[:max_chars])
    finally:
        doc.close()
    return texts


def _clean_narration_source(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        lower = line.lower()
        if "@" in line:
            continue
        if lower.startswith("arxiv:") or lower.startswith("doi:"):
            continue
        if lower in {"references", "acknowledgements", "acknowledgments"}:
            continue
        if lower.startswith("received ") or lower.startswith("accepted "):
            continue
        if "university" in lower and len(line.split()) > 6:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _page_narration(title: str, page_num: int, page_text: str) -> str:
    cleaned_text = _clean_narration_source(page_text)
    if not cleaned_text.strip():
        return f"Page {page_num} of {title}. This page is primarily visual or has limited extractable text."

    prompt = f"""You are writing narration for a research slideshow video.

Write 2 to 4 concise spoken sentences for a voiceover describing this page.
Speak like a research presenter giving the scientific takeaway, not like a screen reader.
Focus on:
- the scientific question
- the method or figure takeaway
- the result or why the page matters

Strict rules:
- Never read author names, affiliations, email addresses, dates, journal boilerplate, arXiv IDs, copyright notices, references, or acknowledgements.
- If this looks like a title or abstract page, summarize only the paper's main idea and importance.
- Do not list section headings or read the text verbatim.
- Prefer plain spoken wording over paper jargon when possible.

Title: {title}
Page: {page_num}

Page text:
{cleaned_text}
"""
    try:
        return gemini_generate(prompt=prompt, timeout=120.0).strip()
    except Exception:
        clipped = cleaned_text[:380].strip()
        if not clipped:
            return f"Page {page_num} of {title}."
        return clipped


def _piper_tts_to_wav(text: str, out_path: Path) -> None:
    piper_bin = shutil.which(PIPER_BIN) or PIPER_BIN
    if not Path(piper_bin).exists() and not shutil.which(PIPER_BIN):
        raise RuntimeError("piper TTS binary is not installed")

    model_path = Path(PIPER_MODEL).expanduser()
    if not model_path.is_absolute():
        model_path = Path.cwd() / model_path
    if not model_path.exists():
        raise RuntimeError(f"Piper model not found: {model_path}")

    cmd = [
        str(piper_bin),
        "--model",
        str(model_path),
        "--output_file",
        str(out_path),
    ]
    subprocess.run(
        cmd,
        input=text,
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _build_segment(image_path: Path, audio_path: Path, out_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _concat_segments(segment_paths: list[Path], out_path: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for path in segment_paths:
            f.write(f"file '{path.as_posix()}'\n")
        concat_path = Path(f.name)
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        concat_path.unlink(missing_ok=True)


def build_narrated_video(pdf_path: Path, out_path: Path, title: str) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="report-video-") as tmp:
        tmp_dir = Path(tmp)
        page_dir = tmp_dir / "pages"
        audio_dir = tmp_dir / "audio"
        segment_dir = tmp_dir / "segments"
        page_dir.mkdir()
        audio_dir.mkdir()
        segment_dir.mkdir()

        image_paths = _render_pdf_pages(pdf_path, page_dir)
        page_texts = _extract_page_texts(pdf_path)
        if not image_paths:
            raise RuntimeError("No PDF pages found to render")

        segment_paths: list[Path] = []
        for idx, image_path in enumerate(image_paths):
            page_num = idx + 1
            narration = _page_narration(
                title=title,
                page_num=page_num,
                page_text=page_texts[idx] if idx < len(page_texts) else "",
            )
            audio_path = audio_dir / f"page-{page_num:03d}.wav"
            _piper_tts_to_wav(narration, audio_path)
            segment_path = segment_dir / f"segment-{page_num:03d}.mp4"
            _build_segment(image_path, audio_path, segment_path)
            segment_paths.append(segment_path)

        _concat_segments(segment_paths, out_path)
    return out_path
