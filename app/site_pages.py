from __future__ import annotations

import html
import re
import shutil
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Markdown → HTML
# ---------------------------------------------------------------------------

def _linkify(text: str) -> str:
    """Wrap bare https?:// URLs in markdown link syntax before conversion."""
    return re.sub(
        r'(?<!\()(https?://[^\s\)\]"<>]+)',
        r'[\1](\1)',
        text,
    )


def _md_to_html(md_text: str) -> str:
    """Convert markdown to HTML, with a capable fallback if markdown isn't installed."""
    try:
        import markdown  # type: ignore
        text = _linkify(md_text)
        return markdown.markdown(
            text,
            extensions=["extra", "sane_lists", "toc"],
            extension_configs={
                "toc": {"title": "Contents", "toc_depth": 2},
            },
        )
    except ImportError:
        return _md_to_html_fallback(md_text)


def _md_to_html_fallback(md_text: str) -> str:
    """Basic markdown → HTML covering headers, bold, italic, lists, hr, and links."""
    def _inline(text: str) -> str:
        # Linkify bare URLs first
        text = re.sub(r'(?<!\()(https?://[^\s\)\]"<>]+)', r'<a href="\1">\1</a>', text)
        # [label](url)
        text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2">\1</a>', text)
        # **bold**
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # *italic* and _italic_
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        text = re.sub(r'_([^_]+)_', r'<em>\1</em>', text)
        # `code`
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
        return text

    lines = md_text.splitlines()
    out: list[str] = []
    in_list = False
    buf: list[str] = []

    def flush_para():
        nonlocal buf
        if buf:
            content = _inline(html.escape(" ".join(buf)))
            out.append(f"<p>{content}</p>")
            buf = []

    def flush_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in lines:
        # Horizontal rule
        if re.match(r"^-{3,}$|^\*{3,}$|^_{3,}$", line.strip()):
            flush_para(); flush_list()
            out.append("<hr>")
            continue

        # ATX headers
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush_para(); flush_list()
            level = len(m.group(1))
            text = _inline(html.escape(m.group(2).strip()))
            out.append(f"<h{level}>{text}</h{level}>")
            continue

        # Unordered list item
        m = re.match(r"^[-*+]\s+(.*)", line)
        if m:
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            text = _inline(html.escape(m.group(1)))
            out.append(f"<li>{text}</li>")
            continue

        # Blank line
        if not line.strip():
            flush_para(); flush_list()
            continue

        # Regular text — accumulate into paragraph
        flush_list()
        buf.append(line.strip())

    flush_para(); flush_list()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Shared CSS / HTML helpers
# ---------------------------------------------------------------------------

_SHARED_CSS = """
  :root {
    color-scheme: light;
    --bg: #f3efe5;
    --paper: #fffdf8;
    --ink: #1f2937;
    --muted: #5b6472;
    --line: #d8d1c2;
    --accent: #0f766e;
    --accent-strong: #134e4a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: Georgia, "Times New Roman", serif;
    color: var(--ink);
    background:
      radial-gradient(circle at top left, rgba(15,118,110,0.12), transparent 28rem),
      linear-gradient(180deg, #f8f5ee 0%, var(--bg) 100%);
    min-height: 100vh;
  }
  a { color: var(--accent-strong); text-decoration-thickness: 1px; text-underline-offset: 0.15em; }
  a:hover { color: var(--accent); }
"""

_INDEX_CSS = _SHARED_CSS + """
  main { max-width: 52rem; margin: 0 auto; padding: 3rem 1.25rem 4rem; }
  .hero {
    background: var(--paper);
    border: 1px solid var(--line);
    border-radius: 1.25rem;
    padding: 2rem;
    box-shadow: 0 18px 45px rgba(31,41,55,0.08);
  }
  h1 { margin: 0 0 0.75rem; font-size: clamp(2rem,4vw,3.5rem); line-height: 1.05; letter-spacing: -0.03em; }
  p { margin: 0; color: var(--muted); font-size: 1.05rem; line-height: 1.6; }
  .cta-row { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 1.25rem; }
  .btn {
    display: inline-block; padding: 0.8rem 1.2rem; border-radius: 999px;
    background: var(--accent); color: white; text-decoration: none; font-weight: 700;
  }
  .btn:hover { background: var(--accent-strong); color: white; }
  .btn.outline {
    background: transparent; color: var(--accent-strong);
    border: 1px solid var(--line);
  }
  .btn.outline:hover { background: rgba(15,118,110,0.08); }
  section { margin-top: 1.5rem; background: rgba(255,253,248,0.88); border: 1px solid var(--line); border-radius: 1.25rem; padding: 1.5rem; }
  h2 { margin: 0 0 1rem; font-size: 1.15rem; text-transform: uppercase; letter-spacing: 0.08em; }
  ul { list-style: none; padding: 0; margin: 0; }
  li { display: flex; gap: 0.75rem; justify-content: space-between; align-items: baseline; padding: 0.9rem 0; border-top: 1px solid var(--line); }
  li:first-child { border-top: 0; padding-top: 0; }
  li:last-child { padding-bottom: 0; }
  .li-links { display: flex; gap: 0.5rem; align-items: baseline; flex-wrap: wrap; }
  .li-links a { font-size: 0.9rem; }
  .li-meta { color: var(--muted); white-space: nowrap; font-size: 0.9rem; }
  .empty { color: var(--muted); }
  @media (max-width: 640px) {
    li { flex-direction: column; align-items: flex-start; }
    .li-meta { white-space: normal; }
  }
"""

_REPORT_CSS = _SHARED_CSS + """
  body { padding: 0; }
  .topbar {
    position: sticky; top: 0; z-index: 10;
    background: rgba(255,253,248,0.92); backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--line);
    padding: 0.75rem 1.5rem;
    display: flex; justify-content: space-between; align-items: center;
    gap: 1rem;
  }
  .topbar-left { display: flex; align-items: center; gap: 1rem; }
  .topbar-title { font-size: 0.95rem; color: var(--muted); font-style: italic; }
  .back-link { font-size: 0.9rem; white-space: nowrap; }
  .topbar-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
  .btn-sm {
    display: inline-block; padding: 0.35rem 0.85rem; border-radius: 999px;
    background: var(--accent); color: white; text-decoration: none;
    font-size: 0.85rem; font-weight: 600;
  }
  .btn-sm:hover { background: var(--accent-strong); color: white; }
  .btn-sm.outline {
    background: transparent; color: var(--accent-strong);
    border: 1px solid var(--line);
  }
  .btn-sm.outline:hover { background: rgba(15,118,110,0.08); }
  article {
    max-width: 52rem; margin: 0 auto; padding: 2.5rem 1.5rem 5rem;
  }
  article h1 { font-size: clamp(1.6rem,3.5vw,2.5rem); line-height: 1.1; letter-spacing: -0.02em; margin: 0 0 1.5rem; }
  article h2 { font-size: 1.35rem; margin: 2.5rem 0 0.75rem; padding-top: 1.5rem; border-top: 1px solid var(--line); letter-spacing: -0.01em; }
  article h2:first-of-type { border-top: none; padding-top: 0; }
  article h3 { font-size: 1.1rem; margin: 1.75rem 0 0.5rem; color: var(--accent-strong); }
  article h4 { font-size: 0.95rem; margin: 1.25rem 0 0.25rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
  article p { line-height: 1.75; margin: 0.75rem 0; }
  article ul, article ol { line-height: 1.7; margin: 0.5rem 0 0.5rem 1.25rem; padding: 0; }
  article li { display: list-item; border: none; padding: 0.15rem 0; font-size: 1rem; }
  article li::marker { color: var(--accent); }
  article hr { border: none; border-top: 1px solid var(--line); margin: 2rem 0; }
  article strong { color: var(--ink); }
  article code { font-size: 0.9em; background: rgba(0,0,0,0.05); padding: 0.1em 0.3em; border-radius: 3px; }
  article blockquote { border-left: 3px solid var(--accent); margin: 1rem 0; padding: 0.25rem 1rem; color: var(--muted); }
  .report-meta { color: var(--muted); font-size: 0.95rem; margin-bottom: 2rem; }
  @media (max-width: 640px) {
    .topbar { flex-direction: column; align-items: flex-start; }
    article { padding: 1.5rem 1rem 4rem; }
  }
"""


# ---------------------------------------------------------------------------
# Per-report HTML page
# ---------------------------------------------------------------------------

def _report_date_from_name(path: Path) -> str:
    stem = path.stem
    if stem.startswith("daily-"):
        return stem.removeprefix("daily-")
    return stem


def _title_for_report(path: Path) -> str:
    label = _report_date_from_name(path)
    try:
        return datetime.strptime(label, "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        return label


def _build_report_page(
    md_path: Path,
    out_path: Path,
    report_date: str,
    human_date: str,
    pdf_href: str | None,
    video_href: str | None,
) -> None:
    md_text = md_path.read_text(encoding="utf-8")
    body_html = _md_to_html(md_text)

    pdf_btn = (
        f'<a class="btn-sm outline" href="{html.escape(pdf_href)}">PDF</a>'
        if pdf_href else ""
    )
    video_btn = (
        f'<a class="btn-sm outline" href="{html.escape(video_href)}">Video</a>'
        if video_href else ""
    )

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Research Digest — {html.escape(human_date)}</title>
  <style>{_REPORT_CSS}</style>
</head>
<body>
  <div class="topbar">
    <div class="topbar-left">
      <a class="back-link" href="../index.html">← All reports</a>
      <span class="topbar-title">{html.escape(human_date)}</span>
    </div>
    <div class="topbar-actions">
      {pdf_btn}
      {video_btn}
    </div>
  </div>
  <article>
    {body_html}
  </article>
</body>
</html>
"""
    out_path.write_text(page, encoding="utf-8")


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------

def build_pages_site(
    reports_dir: Path = Path("data/reports"),
    videos_dir: Path = Path("data/videos"),
    site_dir: Path = Path("docs"),
) -> Path:
    md_paths = sorted(reports_dir.glob("daily-*.md"), reverse=True)
    pdf_paths = {p.stem: p for p in reports_dir.glob("daily-*.pdf")}
    video_paths = {p.stem: p for p in videos_dir.glob("daily-*.mp4")}

    site_dir.mkdir(parents=True, exist_ok=True)
    reports_site_dir = site_dir / "reports"
    videos_site_dir = site_dir / "videos"
    reports_site_dir.mkdir(parents=True, exist_ok=True)
    videos_site_dir.mkdir(parents=True, exist_ok=True)

    # Clear old site artifacts
    for existing in reports_site_dir.glob("*.pdf"):
        existing.unlink()
    for existing in reports_site_dir.glob("*.html"):
        existing.unlink()
    for existing in videos_site_dir.glob("*.mp4"):
        existing.unlink()

    # Copy PDFs and videos
    for stem, pdf_path in pdf_paths.items():
        shutil.copy2(pdf_path, reports_site_dir / pdf_path.name)
    for stem, video_path in video_paths.items():
        shutil.copy2(video_path, videos_site_dir / video_path.name)

    # Copy stable latest files
    latest_pdf = reports_dir / "latest.pdf"
    latest_video = videos_dir / "latest.mp4"
    if latest_pdf.exists():
        shutil.copy2(latest_pdf, site_dir / "latest.pdf")
    if latest_video.exists():
        shutil.copy2(latest_video, site_dir / "latest.mp4")

    # Build per-report HTML pages
    latest_html_href: str | None = None
    rows = []

    for md_path in md_paths:
        stem = md_path.stem          # e.g. "daily-2026-04-22"
        report_date = _report_date_from_name(md_path)
        human_date = _title_for_report(md_path)

        pdf_path = pdf_paths.get(stem)
        video_path = video_paths.get(stem)

        # Relative hrefs from the reports/ sub-directory
        pdf_site_href = f"{html.escape(stem)}.pdf" if pdf_path else None
        video_site_href = f"../videos/{html.escape(stem)}.mp4" if video_path else None

        html_name = f"{stem}.html"
        html_out = reports_site_dir / html_name
        _build_report_page(
            md_path=md_path,
            out_path=html_out,
            report_date=report_date,
            human_date=human_date,
            pdf_href=pdf_site_href,
            video_href=video_site_href,
        )

        html_href = f"reports/{html_name}"
        if latest_html_href is None:
            latest_html_href = html_href

        # Archive row links
        links = [f'<a href="{html.escape(html_href)}">Read</a>']
        if pdf_path:
            size_mb = pdf_path.stat().st_size / (1024 * 1024)
            links.append(f'<a href="reports/{html.escape(stem)}.pdf">PDF · {size_mb:.1f} MB</a>')
        if video_path:
            links.append(f'<a href="videos/{html.escape(stem)}.mp4">Video</a>')

        rows.append(
            f'<li>'
            f'<div class="li-links">{" ".join(links)}</div>'
            f'<span class="li-meta">{html.escape(human_date)}</span>'
            f'</li>'
        )

    # Copy latest HTML to a stable path
    if latest_html_href:
        latest_html_src = site_dir / latest_html_href
        if latest_html_src.exists():
            shutil.copy2(latest_html_src, site_dir / "latest.html")

    # Hero CTA buttons
    if latest_html_href:
        cta = (
            f'<a class="btn" href="{html.escape(latest_html_href)}">Read latest report</a>\n'
            + ('<a class="btn outline" href="latest.pdf">PDF</a>\n' if latest_pdf.exists() else "")
            + ('<a class="btn outline" href="latest.mp4">Video</a>\n' if latest_video.exists() else "")
        )
    elif latest_pdf.exists():
        cta = '<a class="btn" href="latest.pdf">Open latest PDF</a>\n'
        if latest_video.exists():
            cta += '<a class="btn outline" href="latest.mp4">Video</a>\n'
    else:
        cta = '<p class="empty">No reports published yet.</p>'

    archive_markup = "\n".join(rows) if rows else '<p class="empty">No reports published yet.</p>'

    index_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Daily Research Reports</title>
  <style>{_INDEX_CSS}</style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>Daily Research Reports</h1>
      <p>AI-synthesised astrophysics briefings: galaxy clusters, galaxies, gravitational lensing, and dark matter.</p>
      <div class="cta-row">
        {cta}
      </div>
    </section>
    <section>
      <h2>Archive</h2>
      <ul>
        {archive_markup}
      </ul>
    </section>
  </main>
</body>
</html>
"""

    (site_dir / "index.html").write_text(index_html, encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    return site_dir / "index.html"
