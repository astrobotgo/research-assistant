import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer


def _normalize_text(text: str) -> str:
    return (
        (text or "")
        .replace("\u2013", "--")
        .replace("\u2014", "---")
        .replace("\u2212", "-")
        .replace("\u2026", "...")
        .replace("\u00a0", " ")
    )


_GREEK_TEXT_REPLACEMENTS = {
    "α": r"$\alpha$",
    "β": r"$\beta$",
    "γ": r"$\gamma$",
    "δ": r"$\delta$",
    "Δ": r"$\Delta$",
    "λ": r"$\lambda$",
    "μ": r"$\mu$",
    "π": r"$\pi$",
    "σ": r"$\sigma$",
    "τ": r"$\tau$",
    "φ": r"$\phi$",
    "ω": r"$\omega$",
}

_GREEK_MATH_REPLACEMENTS = {
    "α": r"\alpha ",
    "β": r"\beta ",
    "γ": r"\gamma ",
    "δ": r"\delta ",
    "Δ": r"\Delta ",
    "λ": r"\lambda ",
    "μ": r"\mu ",
    "π": r"\pi ",
    "σ": r"\sigma ",
    "τ": r"\tau ",
    "φ": r"\phi ",
    "ω": r"\omega ",
}


def _replace_unicode_greek_text(text: str) -> str:
    out = _normalize_text(text)
    for src, repl in _GREEK_TEXT_REPLACEMENTS.items():
        out = out.replace(src, repl)
    return out


def _replace_unicode_greek_math(text: str) -> str:
    out = text
    for src, repl in _GREEK_MATH_REPLACEMENTS.items():
        out = out.replace(src, repl)
    return out


def _clean_md_text(text: str) -> str:
    # Cleanup markdown/LaTeX-ish text for reportlab paragraphs.
    cleaned = _normalize_text(text)
    cleaned = cleaned.replace("**", "").replace("`", "")
    cleaned = cleaned.replace("\\(", "").replace("\\)", "")
    cleaned = cleaned.replace("\\[", "").replace("\\]", "")
    cleaned = re.sub(r"\$(.*?)\$", r"\1", cleaned)
    cleaned = re.sub(r"\\[A-Za-z]+\{([^}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\[A-Za-z]+", "", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return cleaned


def _latex_escape_text(text: str) -> str:
    text = _normalize_text(text)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def _latex_escape_url(text: str) -> str:
    return _latex_escape_text(text).replace("#", r"\#")


def _latex_inline(text: str) -> str:
    text = _normalize_text(text)
    pattern = re.compile(r"(\$\$.*?\$\$|\$.*?\$|\\\[.*?\\\]|\\\(.*?\\\))", re.DOTALL)
    parts: list[str] = []
    last = 0
    for match in pattern.finditer(text):
        if match.start() > last:
            plain = _replace_unicode_greek_text(text[last:match.start()])
            parts.append(_latex_escape_text(plain))
        parts.append(_replace_unicode_greek_math(match.group(0)))
        last = match.end()
    if last < len(text):
        plain = _replace_unicode_greek_text(text[last:])
        parts.append(_latex_escape_text(plain))
    return "".join(parts)


def _latex_paragraphs(text: str) -> list[str]:
    raw = _normalize_text(text).strip()
    if not raw:
        return []
    blocks = [block.strip() for block in re.split(r"\n\s*\n", raw) if block.strip()]
    return [_latex_inline(block).replace("\n", " ") for block in blocks]


def _latex_markdown_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    bullet_open = False
    for raw_line in _normalize_text(text).splitlines():
        line = raw_line.strip()
        if not line:
            if bullet_open:
                blocks.append(r"\end{itemize}")
                bullet_open = False
            continue
        if line.startswith("## "):
            if bullet_open:
                blocks.append(r"\end{itemize}")
                bullet_open = False
            blocks.append(rf"\section*{{{_latex_inline(line[3:].strip())}}}")
            continue
        if line.startswith("### "):
            if bullet_open:
                blocks.append(r"\end{itemize}")
                bullet_open = False
            blocks.append(rf"\subsection*{{{_latex_inline(line[4:].strip())}}}")
            continue
        if re.match(r"^[-*]\s+", line):
            if not bullet_open:
                blocks.append(r"\begin{itemize}")
                bullet_open = True
            item = re.sub(r"^[-*]\s+", "", line)
            blocks.append(rf"\item {_latex_inline(item)}")
            continue
        if bullet_open:
            blocks.append(r"\end{itemize}")
            bullet_open = False
        blocks.append(_latex_inline(line))
    if bullet_open:
        blocks.append(r"\end{itemize}")
    return blocks


def _scaled_reportlab_image(
    fig_path: Path,
    max_width: float = 5.8 * inch,
    max_height: float = 4.8 * inch,
) -> Image:
    img = Image(str(fig_path))
    width = float(getattr(img, "imageWidth", 0) or 0)
    height = float(getattr(img, "imageHeight", 0) or 0)
    if width <= 0 or height <= 0:
        img.drawWidth = max_width
        img.drawHeight = max_height
        return img

    scale = min(max_width / width, max_height / height, 1.0)
    img.drawWidth = width * scale
    img.drawHeight = height * scale
    return img


def _build_reportlab_pdf(
    out_path: Path,
    report_date: str,
    digest_md: str,
    papers: list[dict],
    figure_map: dict[str, list[dict]],
    page_summary_map: dict[str, list[dict]],
) -> None:
    doc = SimpleDocTemplate(str(out_path), pagesize=LETTER)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"Daily Research Digest - {report_date}", styles["Title"]))
    story.append(
        Paragraph(
            "Topics: galaxy clusters, galaxies, gravitational lensing, dark matter",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    if digest_md:
        story.append(Paragraph("Synthesis", styles["Heading2"]))
        for block in digest_md.split("\n\n"):
            cleaned = _clean_md_text(block.strip())
            if not cleaned:
                continue
            story.append(Paragraph(cleaned, styles["Normal"]))
            story.append(Spacer(1, 0.08 * inch))
        story.append(PageBreak())

    story.append(Paragraph("Paper Summaries and Figures", styles["Heading1"]))
    story.append(Spacer(1, 0.15 * inch))

    for idx, p in enumerate(papers, start=1):
        paper_id = p.get("id") or p.get("title", "")
        title = _clean_md_text(p.get("title", "Untitled"))
        summary = _clean_md_text(p.get("summary", ""))
        focus = _clean_md_text(p.get("_topic_label", ""))
        authors = ", ".join(p.get("authors", []) or [])

        story.append(Paragraph(f"{idx}. {title}", styles["Heading2"]))
        story.append(Paragraph(f"Focus: {focus}", styles["Normal"]))
        story.append(Paragraph(f"Authors: {_clean_md_text(authors)}", styles["Normal"]))
        story.append(Paragraph(f"Published: {_clean_md_text(p.get('published', ''))}", styles["Normal"]))
        story.append(Paragraph(f"Summary: {summary}", styles["Normal"]))

        page_summaries = page_summary_map.get(paper_id, [])
        if page_summaries:
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph("Page-by-page summaries", styles["Heading3"]))
            for item in page_summaries:
                pnum = item.get("page", "")
                ps = _clean_md_text(item.get("summary", ""))
                story.append(Paragraph(f"Page {pnum}: {ps}", styles["Normal"]))

        fig_items = figure_map.get(paper_id, [])
        if fig_items:
            story.append(Spacer(1, 0.12 * inch))
            story.append(Paragraph("Selected figures", styles["Heading3"]))
            for fig in fig_items:
                fig_path = Path(fig["path"])
                if not fig_path.exists():
                    continue
                try:
                    img = _scaled_reportlab_image(fig_path)
                    story.append(img)
                    reason = _clean_md_text(fig.get("reason", ""))
                    if reason:
                        story.append(Spacer(1, 0.04 * inch))
                        story.append(Paragraph(f"Why this figure matters: {reason}", styles["Italic"]))
                    story.append(Spacer(1, 0.08 * inch))
                except Exception:
                    continue

        story.append(Spacer(1, 0.18 * inch))
        if idx < len(papers):
            story.append(PageBreak())

    doc.build(story)


def _latex_document(
    report_date: str,
    digest_md: str,
    papers: list[dict],
    figure_map: dict[str, list[dict]],
    page_summary_map: dict[str, list[dict]],
) -> str:
    lines = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{lmodern}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage{amsmath,amssymb,mathtools}",
        r"\usepackage{graphicx}",
        r"\usepackage{grffile}",
        r"\usepackage{float}",
        r"\usepackage{hyperref}",
        r"\usepackage{parskip}",
        r"\usepackage{enumitem}",
        r"\usepackage{xurl}",
        r"\setlength{\parskip}{0.65em}",
        r"\setlength{\parindent}{0pt}",
        r"\begin{document}",
        rf"\title{{Daily Research Digest --- { _latex_escape_text(report_date) }}}",
        r"\author{}",
        r"\date{}",
        r"\maketitle",
        r"\textbf{Topics:} galaxy clusters, galaxies, gravitational lensing, dark matter.",
    ]

    if digest_md:
        lines.append(r"\section*{Synthesis}")
        lines.extend(_latex_markdown_blocks(digest_md))
        lines.append(r"\newpage")

    lines.append(r"\section*{Paper Summaries and Figures}")
    for idx, paper in enumerate(papers, start=1):
        paper_id = paper.get("id") or paper.get("title", "")
        title = _latex_inline(paper.get("title", "Untitled"))
        focus = _latex_inline(paper.get("_topic_label", ""))
        authors = _latex_inline(", ".join(paper.get("authors", []) or []))
        published = _latex_inline(paper.get("published", ""))
        summary = _latex_paragraphs(paper.get("summary", ""))

        lines.append(rf"\subsection*{{{idx}. {title}}}")
        if focus:
            lines.append(rf"\textbf{{Focus:}} {focus}\\")
        if authors:
            lines.append(rf"\textbf{{Authors:}} {authors}\\")
        if published:
            lines.append(rf"\textbf{{Published:}} {published}\\")

        pdf_url = paper.get("pdf_url", "")
        if pdf_url:
            lines.append(
                rf"\textbf{{PDF:}} \href{{{_latex_escape_url(pdf_url)}}}{{{_latex_escape_text(pdf_url)}}}\\"
            )

        lines.append(r"\textbf{Summary:}")
        lines.extend(summary or [r"\emph{No summary available.}"])

        page_summaries = page_summary_map.get(paper_id, [])
        if page_summaries:
            lines.append(r"\subsubsection*{Page-by-page summaries}")
            lines.append(r"\begin{itemize}[leftmargin=*,itemsep=0.35em]")
            for item in page_summaries:
                pnum = item.get("page", "")
                ps = _latex_inline(item.get("summary", ""))
                lines.append(rf"\item \textbf{{Page {pnum}:}} {ps}")
            lines.append(r"\end{itemize}")

        fig_items = figure_map.get(paper_id, [])
        fig_items = [item for item in fig_items if Path(item["path"]).exists()]
        if fig_items:
            lines.append(r"\subsubsection*{Selected figures}")
            for item in fig_items:
                fig_path = Path(item["path"])
                abs_path = fig_path.resolve().as_posix()
                lines.extend(
                    [
                        r"\begin{figure}[H]",
                        r"\centering",
                        rf"\includegraphics[width=\linewidth,height=0.42\textheight,keepaspectratio]{{{_latex_escape_text(abs_path)}}}",
                        rf"\caption*{{Why this figure matters: {_latex_inline(item.get('reason', ''))}}}" if item.get("reason") else "",
                        r"\end{figure}",
                    ]
                )

        if idx < len(papers):
            lines.append(r"\newpage")

    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def _find_latex_engine() -> str | None:
    env_engine = (os.environ.get("RESEARCH_ASSISTANT_LATEX") or "").strip()
    if env_engine and Path(env_engine).exists():
        return env_engine

    for candidate in (
        shutil.which("tectonic"),
        str(Path.home() / ".local/bin/tectonic"),
        shutil.which("pdflatex"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _build_latex_pdf(
    out_path: Path,
    report_date: str,
    digest_md: str,
    papers: list[dict],
    figure_map: dict[str, list[dict]],
    page_summary_map: dict[str, list[dict]],
) -> None:
    engine = _find_latex_engine()
    if not engine:
        raise RuntimeError("No LaTeX engine found")

    tex_path = out_path.with_suffix(".tex")
    tex_source = _latex_document(
        report_date=report_date,
        digest_md=digest_md,
        papers=papers,
        figure_map=figure_map,
        page_summary_map=page_summary_map,
    )
    tex_path.write_text(tex_source, encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="ra-latex-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        tmp_tex = tmpdir_path / tex_path.name
        tmp_pdf = tmpdir_path / out_path.name
        tmp_tex.write_text(tex_source, encoding="utf-8")

        cmd = [engine]
        if Path(engine).name == "tectonic":
            cmd.extend(
                [
                    "--outdir",
                    str(tmpdir_path),
                    "--keep-logs",
                    str(tmp_tex),
                ]
            )
        else:
            cmd.extend(
                [
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    f"-output-directory={tmpdir_path}",
                    str(tmp_tex),
                ]
            )
        subprocess.run(cmd, check=True, cwd=tmpdir_path)
        out_path.write_bytes(tmp_pdf.read_bytes())


def build_daily_pdf_report(
    out_path: Path,
    report_date: str,
    digest_md: str,
    papers: list[dict],
    figure_map: dict[str, list[dict]],
    page_summary_map: dict[str, list[dict]],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _build_latex_pdf(
            out_path=out_path,
            report_date=report_date,
            digest_md=digest_md,
            papers=papers,
            figure_map=figure_map,
            page_summary_map=page_summary_map,
        )
    except Exception:
        _build_reportlab_pdf(
            out_path=out_path,
            report_date=report_date,
            digest_md=digest_md,
            papers=papers,
            figure_map=figure_map,
            page_summary_map=page_summary_map,
        )
