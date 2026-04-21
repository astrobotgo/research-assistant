from __future__ import annotations

import html
import shutil
from datetime import datetime
from pathlib import Path


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


def build_pages_site(
    reports_dir: Path = Path("data/reports"),
    videos_dir: Path = Path("data/videos"),
    site_dir: Path = Path("docs"),
) -> Path:
    pdf_paths = sorted(reports_dir.glob("daily-*.pdf"), reverse=True)
    video_paths = {
        path.stem: path for path in sorted(videos_dir.glob("daily-*.mp4"), reverse=True)
    }

    site_dir.mkdir(parents=True, exist_ok=True)
    reports_site_dir = site_dir / "reports"
    videos_site_dir = site_dir / "videos"
    reports_site_dir.mkdir(parents=True, exist_ok=True)
    videos_site_dir.mkdir(parents=True, exist_ok=True)

    for existing in reports_site_dir.glob("*.pdf"):
        existing.unlink()
    for existing in videos_site_dir.glob("*.mp4"):
        existing.unlink()

    for pdf_path in pdf_paths:
        shutil.copy2(pdf_path, reports_site_dir / pdf_path.name)
    for video_path in video_paths.values():
        shutil.copy2(video_path, videos_site_dir / video_path.name)

    latest_pdf = reports_dir / "latest.pdf"
    latest_video = videos_dir / "latest.mp4"
    if latest_pdf.exists():
        shutil.copy2(latest_pdf, site_dir / "latest.pdf")
    if latest_video.exists():
        shutil.copy2(latest_video, site_dir / "latest.mp4")

    rows = []
    for pdf_path in pdf_paths:
        label = _title_for_report(pdf_path)
        rel_href = f"reports/{html.escape(pdf_path.name)}"
        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        video_path = video_paths.get(pdf_path.stem)
        video_link = ""
        if video_path:
            video_href = f"videos/{html.escape(video_path.name)}"
            video_link = f' <a class="secondary-link" href="{video_href}">video</a>'
        rows.append(
            "<li>"
            f"<div><a href=\"{rel_href}\">{html.escape(label)}</a>{video_link}</div>"
            f"<span>{html.escape(pdf_path.name)} · {size_mb:.1f} MB</span>"
            "</li>"
        )

    latest_link = (
        '<a class="latest-link" href="latest.pdf">Open latest report</a>'
        if latest_pdf.exists()
        else "<p class=\"empty\">No latest PDF found yet.</p>"
    )
    latest_video_link = (
        '<a class="latest-link secondary" href="latest.mp4">Watch latest video</a>'
        if latest_video.exists()
        else ""
    )
    archive_markup = "\n".join(rows) if rows else "<p class=\"empty\">No reports published yet.</p>"

    index_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Daily Research Reports</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3efe5;
      --paper: #fffdf8;
      --ink: #1f2937;
      --muted: #5b6472;
      --line: #d8d1c2;
      --accent: #0f766e;
      --accent-strong: #134e4a;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.12), transparent 28rem),
        linear-gradient(180deg, #f8f5ee 0%, var(--bg) 100%);
    }}
    main {{
      max-width: 52rem;
      margin: 0 auto;
      padding: 3rem 1.25rem 4rem;
    }}
    .hero {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 1.25rem;
      padding: 2rem;
      box-shadow: 0 18px 45px rgba(31, 41, 55, 0.08);
    }}
    h1 {{
      margin: 0 0 0.75rem;
      font-size: clamp(2rem, 4vw, 3.5rem);
      line-height: 1.05;
      letter-spacing: -0.03em;
    }}
    p {{
      margin: 0;
      color: var(--muted);
      font-size: 1.05rem;
      line-height: 1.6;
    }}
    .latest-link {{
      display: inline-block;
      margin-top: 1.25rem;
      padding: 0.8rem 1.1rem;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      text-decoration: none;
      font-weight: 700;
    }}
    .latest-link:hover {{
      background: var(--accent-strong);
    }}
    .latest-link.secondary {{
      background: transparent;
      color: var(--accent-strong);
      border: 1px solid var(--line);
      margin-left: 0.75rem;
    }}
    .latest-link.secondary:hover {{
      background: rgba(15, 118, 110, 0.08);
    }}
    section {{
      margin-top: 1.5rem;
      background: rgba(255, 253, 248, 0.88);
      border: 1px solid var(--line);
      border-radius: 1.25rem;
      padding: 1.5rem;
      backdrop-filter: blur(8px);
    }}
    h2 {{
      margin: 0 0 1rem;
      font-size: 1.15rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    ul {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    li {{
      display: flex;
      gap: 0.75rem;
      justify-content: space-between;
      align-items: baseline;
      padding: 0.9rem 0;
      border-top: 1px solid var(--line);
    }}
    li:first-child {{
      border-top: 0;
      padding-top: 0;
    }}
    li:last-child {{
      padding-bottom: 0;
    }}
    a {{
      color: var(--accent-strong);
      text-decoration-thickness: 1px;
      text-underline-offset: 0.15em;
    }}
    .secondary-link {{
      margin-left: 0.65rem;
      font-size: 0.9rem;
      color: var(--accent);
    }}
    span {{
      color: var(--muted);
      white-space: nowrap;
      font-size: 0.95rem;
    }}
    .empty {{
      color: var(--muted);
    }}
    @media (max-width: 640px) {{
      li {{
        flex-direction: column;
        align-items: flex-start;
      }}
      span {{
        white-space: normal;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>Daily Research Reports</h1>
      <p>Browse the latest generated PDF digest, or watch an AI-narrated slideshow video built from the report pages and summaries.</p>
      {latest_link}
      {latest_video_link}
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
