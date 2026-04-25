from __future__ import annotations

import html
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

_FIGURES_CACHE = Path("data/cache/figures")  # source
_FIGURES_SITE  = Path("docs/figures")        # destination (web-accessible)


# ---------------------------------------------------------------------------
# Topic metadata
# ---------------------------------------------------------------------------

_TOPICS = [
    {"key": "galaxy_clusters",      "label": "Galaxy Clusters",       "color": "blue",   "section_re": r"galaxy\s+clusters?"},
    {"key": "galaxies",             "label": "Galaxies",               "color": "purple", "section_re": r"galaxies"},
    {"key": "gravitational_lensing","label": "Gravitational Lensing",  "color": "teal",   "section_re": r"gravitational\s+lensing"},
    {"key": "dark_matter",          "label": "Dark Matter",            "color": "amber",  "section_re": r"dark\s+matter"},
]

_COLOR_FOR_KEY   = {t["key"]: t["color"] for t in _TOPICS}
_LABEL_FOR_KEY   = {t["key"]: t["label"] for t in _TOPICS}


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _linkify(text: str) -> str:
    return re.sub(r'(?<!\()(https?://[^\s\)\]"<>]+)', r'[\1](\1)', text)


def _md_to_html(md_text: str) -> str:
    try:
        import markdown  # type: ignore
        return markdown.markdown(
            _linkify(md_text),
            extensions=["extra", "sane_lists"],
        )
    except ImportError:
        return _md_fallback(md_text)


def _md_fallback(md_text: str) -> str:
    def _inline(t: str) -> str:
        t = re.sub(r'(?<!\()(https?://[^\s\)\]"<>]+)', r'<a href="\1">\1</a>', t)
        t = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2">\1</a>', t)
        t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
        t = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         t)
        t = re.sub(r'`([^`]+)`',     r'<code>\1</code>',     t)
        return t

    lines = md_text.splitlines()
    out: list[str] = []
    in_ul = False
    buf: list[str] = []

    def flush_p():
        nonlocal buf
        if buf:
            out.append(f'<p>{_inline(html.escape(" ".join(buf)))}</p>')
            buf = []

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for line in lines:
        if re.match(r"^-{3,}$", line.strip()):
            flush_p(); close_ul(); out.append("<hr>"); continue
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush_p(); close_ul()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(html.escape(m.group(2).strip()))}</h{lvl}>")
            continue
        m = re.match(r"^[-*+]\s+(.*)", line)
        if m:
            flush_p()
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{_inline(html.escape(m.group(1)))}</li>")
            continue
        if not line.strip():
            flush_p(); close_ul(); continue
        close_ul(); buf.append(line.strip())

    flush_p(); close_ul()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Markdown section extraction
# ---------------------------------------------------------------------------

def _extract_section(md_text: str, heading_re: str) -> str:
    """Return the body of the first ## heading matching heading_re (case-insensitive)."""
    m = re.search(rf'^##\s+{heading_re}\s*$', md_text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    nxt = re.search(r'^##\s+', md_text[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(md_text)
    return md_text[start:end].strip()


def _extract_briefing_block(md_text: str) -> str:
    """Return just the synthesized briefing portion (between its wrapper and ## Catalog)."""
    m = re.search(r'^##\s+Research briefing.*$', md_text, re.MULTILINE | re.IGNORECASE)
    start = m.end() if m else 0
    cat = re.search(r'^##\s+Catalog', md_text[start:], re.MULTILINE | re.IGNORECASE)
    end = start + cat.start() if cat else len(md_text)
    return md_text[start:end]


# ---------------------------------------------------------------------------
# Paper helpers
# ---------------------------------------------------------------------------

def _arxiv_url(paper: dict) -> str:
    pid = paper.get("id") or paper.get("pdf_url") or ""
    pid = pid.replace("http://", "https://")
    if "arxiv.org" in pid:
        # normalise to abstract page
        return re.sub(r'/pdf/([^v]+)(v\d+)?(.pdf)?$', r'/abs/\1', pid)
    return pid


def _truncate_abstract(text: str, max_chars: int = 300) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    chunk = text[:max_chars]
    cut = max(chunk.rfind(". "), chunk.rfind("! "), chunk.rfind("? "))
    return (chunk[: cut + 1] if cut > max_chars * 0.5 else chunk.rstrip()) + "…"


def _format_authors(authors: list) -> str:
    if not authors:
        return ""
    if len(authors) <= 3:
        return ", ".join(authors)
    return f"{authors[0]} et al."


def _paper_date(paper: dict) -> str:
    raw = paper.get("published") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%b %d")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
:root {
  --bg:           #f5f3ee;
  --paper:        #ffffff;
  --ink:          #18181b;
  --muted:        #71717a;
  --line:         #e4e0d8;
  --accent:       #0f766e;
  --accent-dark:  #115e59;

  --blue:         #1d4ed8; --blue-bg:   #eff6ff;
  --purple:       #7c3aed; --purple-bg: #f5f3ff;
  --teal:         #0f766e; --teal-bg:   #f0fdfa;
  --amber:        #b45309; --amber-bg:  #fffbeb;

  --header-h: 56px;
  --r: 10px;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── Header ── */
.site-header {
  position: sticky; top: 0; z-index: 100;
  height: var(--header-h);
  background: rgba(255,255,255,0.94);
  backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--line);
}
.header-inner {
  max-width: 1200px; margin: 0 auto; padding: 0 1.5rem;
  height: 100%; display: flex; align-items: center; justify-content: space-between; gap: 1rem;
}
.site-brand { display: flex; align-items: center; gap: 0.5rem; }
.site-name { font-weight: 800; font-size: 1rem; letter-spacing: -0.03em; color: var(--accent-dark); }
.site-tagline { font-size: 0.8rem; color: var(--muted); }
.header-right { display: flex; align-items: center; gap: 0.75rem; }
.date-label { font-size: 0.82rem; color: var(--muted); }
.header-pill {
  font-size: 0.75rem; font-weight: 600; padding: 0.22rem 0.65rem;
  border: 1px solid var(--line); border-radius: 999px;
  color: var(--muted); background: transparent;
}
.header-pill:hover { border-color: var(--accent); color: var(--accent); text-decoration: none; }

/* ── Page layout ── */
.page-wrap {
  max-width: 1200px; margin: 0 auto;
  padding: 2rem 1.5rem 5rem;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 2rem;
  align-items: start;
}
.main-col { min-width: 0; }

/* ── Overview ── */
.overview-card {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 1.75rem;
  margin-bottom: 2.25rem;
}
.eyebrow {
  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--accent);
  margin-bottom: 0.75rem;
}
.overview-body {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 1.05rem; line-height: 1.82; color: #27272a;
}
.overview-body p + p { margin-top: 0.6rem; }

/* ── Topic section ── */
.topic-section { margin-bottom: 2.5rem; }
.topic-header {
  display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.9rem;
}
.topic-badge {
  font-size: 0.75rem; font-weight: 700; padding: 0.28rem 0.8rem;
  border-radius: 999px; letter-spacing: 0.02em;
}
.c-blue   { background: var(--blue-bg);   color: var(--blue);   }
.c-purple { background: var(--purple-bg); color: var(--purple); }
.c-teal   { background: var(--teal-bg);   color: var(--teal);   }
.c-amber  { background: var(--amber-bg);  color: var(--amber);  }
.topic-count { font-size: 0.82rem; color: var(--muted); }

.topic-synthesis {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 0.92rem; line-height: 1.78; color: #3f3f46;
  border-left: 3px solid var(--line);
  padding: 0.6rem 1rem;
  margin-bottom: 1.1rem;
  border-radius: 0 4px 4px 0;
}
.topic-synthesis.c-blue   { border-left-color: var(--blue);   }
.topic-synthesis.c-purple { border-left-color: var(--purple); }
.topic-synthesis.c-teal   { border-left-color: var(--teal);   }
.topic-synthesis.c-amber  { border-left-color: var(--amber);  }
.topic-synthesis p + p { margin-top: 0.5rem; }
.topic-synthesis ul { padding-left: 1.2rem; }
.topic-synthesis li { margin-bottom: 0.25rem; }

/* ── Paper cards ── */
.papers-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(270px, 1fr));
  gap: 0.9rem;
}
.paper-card {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 1rem 1.15rem;
  display: flex; flex-direction: column; gap: 0.45rem;
  transition: box-shadow 0.15s ease, border-color 0.15s ease;
}
.paper-card:hover { box-shadow: 0 4px 18px rgba(0,0,0,0.08); border-color: #c8c4bc; }
.card-pills { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.pill {
  font-size: 0.68rem; font-weight: 600; padding: 0.12rem 0.48rem;
  border-radius: 999px; letter-spacing: 0.03em;
}
.pill-blue   { background: var(--blue-bg);   color: var(--blue);   }
.pill-purple { background: var(--purple-bg); color: var(--purple); }
.pill-teal   { background: var(--teal-bg);   color: var(--teal);   }
.pill-amber  { background: var(--amber-bg);  color: var(--amber);  }
.pill-watch  { background: #fef3c7; color: #92400e; }

.card-title {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 0.9rem; font-weight: 600; line-height: 1.4;
}
.card-title a { color: var(--ink); }
.card-title a:hover { color: var(--accent); text-decoration: underline; }
.card-authors { font-size: 0.78rem; color: var(--muted); }
.card-abstract {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 0.83rem; line-height: 1.65; color: #52525b;
  flex: 1;
}
.card-footer {
  display: flex; justify-content: space-between; align-items: center;
  padding-top: 0.3rem; border-top: 1px solid var(--line);
  margin-top: 0.1rem;
}
.card-arxiv { font-size: 0.76rem; font-weight: 600; color: var(--accent); }
.card-arxiv:hover { text-decoration: underline; }
.card-cite { font-size: 0.76rem; color: var(--muted); }

.no-papers { font-size: 0.88rem; color: var(--muted); font-style: italic; padding: 0.5rem 0; }

/* ── Paper figure ── */
.card-figure {
  width: 100%; border-radius: 6px; overflow: hidden;
  background: var(--bg); border: 1px solid var(--line);
  margin-bottom: 0.1rem;
}
.card-figure img {
  width: 100%; height: 160px; object-fit: cover; object-position: center top;
  display: block;
}
.card-figure-caption {
  font-size: 0.72rem; color: var(--muted); line-height: 1.45;
  padding: 0.3rem 0.5rem; font-style: italic;
  border-top: 1px solid var(--line);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ── Related papers ── */
.related-papers {
  border-top: 1px solid var(--line);
  padding-top: 0.5rem; margin-top: 0.2rem;
}
.related-label {
  font-size: 0.67rem; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 0.35rem;
}
.related-list { list-style: none; display: flex; flex-direction: column; gap: 0.22rem; }
.related-list li { font-size: 0.78rem; line-height: 1.4; }
.related-list a { color: var(--accent-dark); }
.related-list .rel-meta { color: var(--muted); font-size: 0.72rem; }

/* ── Open questions ── */
.open-qs-card {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 1.5rem;
  margin-top: 1rem;
  margin-bottom: 2rem;
}
.open-qs-body {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 0.9rem; line-height: 1.75; color: #3f3f46;
}
.open-qs-body ul { padding-left: 1.25rem; }
.open-qs-body li { margin-bottom: 0.35rem; }
.open-qs-body p + p { margin-top: 0.5rem; }

/* ── Sidebar ── */
.sidebar {
  position: sticky;
  top: calc(var(--header-h) + 1.25rem);
  display: flex; flex-direction: column; gap: 1.1rem;
  max-height: calc(100vh - var(--header-h) - 2.5rem);
  overflow-y: auto;
  scrollbar-width: thin;
  padding-right: 2px;
}
.sidebar-card {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 1rem 1.1rem;
}
.sidebar-title {
  font-size: 0.67rem; font-weight: 700; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--muted);
  padding-bottom: 0.5rem; margin-bottom: 0.75rem;
  border-bottom: 1px solid var(--line);
}
.ctx-body {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 0.82rem; line-height: 1.68; color: #52525b;
}
.ctx-body p + p { margin-top: 0.4rem; }
.ctx-body ul { padding-left: 1.1rem; }
.ctx-body li { margin-bottom: 0.22rem; }
.ctx-body h2, .ctx-body h3 { font-size: 0.82rem; margin: 0.5rem 0 0.3rem; color: var(--ink); }

.must-list { list-style: none; }
.must-list li {
  border-top: 1px solid var(--line); padding: 0.55rem 0;
  font-size: 0.81rem; line-height: 1.5;
}
.must-list li:first-child { border-top: none; padding-top: 0; }

.archive-list { list-style: none; }
.archive-list li { border-top: 1px solid var(--line); }
.archive-list li:first-child { border-top: none; }
.archive-list a {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.45rem 0; font-size: 0.82rem; color: var(--ink);
}
.archive-list a:hover { color: var(--accent); text-decoration: none; }
.arc-label { font-weight: 500; }
.arc-date { font-size: 0.77rem; color: var(--muted); }

/* ── Report page nav ── */
.report-topbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; margin-bottom: 1.75rem; flex-wrap: wrap;
}
.back-link { font-size: 0.85rem; color: var(--accent); }
.report-actions { display: flex; gap: 0.5rem; }
.action-pill {
  font-size: 0.77rem; font-weight: 600; padding: 0.28rem 0.75rem;
  border: 1px solid var(--line); border-radius: 999px; color: var(--muted);
}
.action-pill:hover { border-color: var(--accent); color: var(--accent); text-decoration: none; }

/* ── Clickable card cursor ── */
.paper-card { cursor: pointer; }

/* ── Expand hint ── */
.card-expand-hint {
  font-size: 0.72rem; color: var(--accent); font-weight: 500;
  text-align: right; margin-top: 0.1rem; opacity: 0;
  transition: opacity 0.15s;
}
.paper-card:hover .card-expand-hint { opacity: 1; }

/* ── Modal ── */
.modal-overlay {
  position: fixed; inset: 0; z-index: 200;
  background: rgba(24,24,27,0.6);
  backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
  padding: 1rem;
}
.modal-overlay[hidden] { display: none; }
.modal-box {
  background: var(--paper);
  border-radius: 14px;
  width: 100%; max-width: 680px;
  max-height: 90vh;
  overflow-y: auto;
  position: relative;
  box-shadow: 0 24px 64px rgba(0,0,0,0.25);
}
.modal-close {
  position: sticky; top: 0; float: right;
  margin: 0.75rem 0.75rem 0 0;
  background: var(--bg); border: 1px solid var(--line);
  border-radius: 999px; width: 2rem; height: 2rem;
  font-size: 1rem; cursor: pointer; color: var(--muted);
  display: flex; align-items: center; justify-content: center;
  z-index: 1;
}
.modal-close:hover { color: var(--ink); }
.modal-inner { padding: 0 1.75rem 1.75rem; clear: both; }
.modal-fig { width: 100%; border-radius: 8px; overflow: hidden; margin-bottom: 0.75rem; }
.modal-fig img { width: 100%; max-height: 340px; object-fit: contain; background: var(--bg); display: block; }
.modal-fig-cap {
  font-size: 0.78rem; color: var(--muted); font-style: italic;
  line-height: 1.5; padding: 0.4rem 0; border-top: 1px solid var(--line);
}
.modal-pills { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-bottom: 0.6rem; }
.modal-title {
  font-family: Georgia, serif; font-size: 1.15rem; line-height: 1.4;
  margin-bottom: 0.4rem; font-weight: 700;
}
.modal-title a { color: var(--ink); }
.modal-title a:hover { color: var(--accent); text-decoration: underline; }
.modal-meta { font-size: 0.82rem; color: var(--muted); margin-bottom: 1rem; }
.modal-section-title {
  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--muted);
  margin: 1.1rem 0 0.5rem; padding-top: 1rem;
  border-top: 1px solid var(--line);
}
.modal-abstract {
  font-family: Georgia, serif; font-size: 0.93rem;
  line-height: 1.78; color: #3f3f46;
}
.modal-analysis {
  font-family: Georgia, serif; font-size: 0.88rem;
  line-height: 1.7; color: #52525b;
}
.modal-related ul { list-style: none; display: flex; flex-direction: column; gap: 0.3rem; }
.modal-related li { font-size: 0.83rem; line-height: 1.45; }
.modal-related a { color: var(--accent-dark); }
.modal-related span { color: var(--muted); font-size: 0.78rem; }
.modal-footer { margin-top: 1.25rem; padding-top: 1rem; border-top: 1px solid var(--line); }
.modal-arxiv-btn {
  display: inline-block; padding: 0.55rem 1.1rem;
  background: var(--accent); color: white; border-radius: 999px;
  font-size: 0.85rem; font-weight: 600; text-decoration: none;
}
.modal-arxiv-btn:hover { background: var(--accent-dark); text-decoration: none; }

/* ── Responsive ── */
@media (max-width: 960px) {
  .page-wrap { grid-template-columns: 1fr; }
  .sidebar { position: static; max-height: none; overflow-y: visible; }
}
@media (max-width: 600px) {
  .header-tagline, .date-label { display: none; }
  .papers-grid { grid-template-columns: 1fr; }
  .page-wrap { padding: 1.25rem 1rem 3rem; }
}
"""


# ---------------------------------------------------------------------------
# Figure helpers
# ---------------------------------------------------------------------------

def _safe_name(text: str) -> str:
    """Mirror of figures.py _safe_name to derive the cache directory key."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip())
    return cleaned.strip("-")[:120] or "paper"


def _figure_site_path(paper: dict) -> tuple[Path | None, str]:
    """
    Return (site_path, caption) for the first extracted figure for this paper,
    or (None, '') if none exists.  Copies the figure into docs/figures/ if needed.
    """
    pid = paper.get("id") or paper.get("title", "")
    if not pid:
        return None, ""
    key = _safe_name(pid)
    src = _FIGURES_CACHE / key / "figure_1.png"
    if not src.exists():
        return None, ""
    dest_dir = _FIGURES_SITE / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "figure_1.png"
    if not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
        shutil.copy2(src, dest)
    # Extract caption from paper's figure_map entry if available
    caption = ""
    figs = paper.get("_figures") or []
    if figs and isinstance(figs[0], dict):
        caption = (figs[0].get("caption") or figs[0].get("reason") or "").strip()
    return dest, caption


# ---------------------------------------------------------------------------
# Component builders
# ---------------------------------------------------------------------------

def _pill_html(label: str, color: str) -> str:
    return f'<span class="pill pill-{color}">{html.escape(label)}</span>'


def _topic_pills(paper: dict) -> str:
    labels = [l.strip() for l in (paper.get("_topic_label") or "").split(",") if l.strip()]
    out = []
    for label in labels:
        color = _COLOR_FOR_KEY.get(paper.get("_topic", ""), "teal")
        # match label to a known topic color
        for t in _TOPICS:
            if t["label"].lower() == label.lower():
                color = t["color"]
                break
        out.append(_pill_html(label.title(), color))
    if paper.get("_watchlisted"):
        out.append(f'<span class="pill pill-watch">★ {html.escape(str(paper["_watchlisted"]).upper())}</span>')
    return "\n".join(out)


def _paper_card(paper: dict, fig_prefix: str = "figures", pid: str = "") -> str:
    url = html.escape(_arxiv_url(paper))
    title = html.escape(paper.get("title") or "Untitled")
    authors = html.escape(_format_authors(paper.get("authors") or []))
    abstract_short = html.escape(_truncate_abstract(paper.get("summary") or ""))
    pills = _topic_pills(paper)
    date = html.escape(_paper_date(paper))
    s2 = paper.get("semantic_scholar") or {}
    cite_count = s2.get("citationCount")
    cite_html = (
        f'<span class="card-cite">{cite_count:,} citations</span>'
        if cite_count else f'<span class="card-cite">{date}</span>'
    )

    # Figure thumbnail
    fig_path, fig_caption = _figure_site_path(paper)
    fig_html = ""
    if fig_path:
        paper_pid = paper.get("id") or paper.get("title", "")
        key = _safe_name(paper_pid)
        img_src = html.escape(f"{fig_prefix}/{key}/figure_1.png")
        cap_html = (
            f'<div class="card-figure-caption">{html.escape(fig_caption)}</div>'
            if fig_caption else ""
        )
        fig_html = f'<div class="card-figure"><img src="{img_src}" alt="Figure" loading="lazy">{cap_html}</div>'

    expand_hint = '<div class="card-expand-hint">Click for full summary ↗</div>' if pid else ""
    pid_attr = f'data-pid="{pid}"' if pid else ""

    return f"""<article class="paper-card" {pid_attr}>
  {fig_html}
  <div class="card-pills">{pills}</div>
  <div class="card-title"><a href="{url}" target="_blank" rel="noopener">{title}</a></div>
  <div class="card-authors">{authors}</div>
  <p class="card-abstract">{abstract_short}</p>
  <div class="card-footer">
    <a class="card-arxiv" href="{url}" target="_blank" rel="noopener">arXiv ↗</a>
    {cite_html}
  </div>
  {expand_hint}
</article>"""


def _topic_block(topic: dict, papers: list[dict], synthesis_html: str, fig_prefix: str = "figures") -> str:
    color = topic["color"]
    label = topic["label"]
    count = len(papers)
    count_str = f"{count} paper{'s' if count != 1 else ''}"

    synthesis_block = (
        f'<div class="topic-synthesis c-{color}">{synthesis_html}</div>'
        if synthesis_html else ""
    )
    if papers:
        cards = "\n".join(_paper_card(p, fig_prefix=fig_prefix, pid=p.get("_pid", "")) for p in papers)
        grid = f'<div class="papers-grid">{cards}</div>'
    else:
        grid = '<p class="no-papers">No papers in today\'s selection for this topic.</p>'

    return f"""<section class="topic-section">
  <div class="topic-header">
    <span class="topic-badge c-{color}">{html.escape(label)}</span>
    <span class="topic-count">{count_str}</span>
  </div>
  {synthesis_block}
  {grid}
</section>"""


def _sidebar_context(context_html: str) -> str:
    if not context_html:
        return ""
    return f"""<div class="sidebar-card">
  <div class="sidebar-title">This week in the field</div>
  <div class="ctx-body">{context_html}</div>
</div>"""


def _sidebar_must_reads(must_reads_html: str) -> str:
    if not must_reads_html:
        return ""
    return f"""<div class="sidebar-card">
  <div class="sidebar-title">Recommended reading</div>
  <div class="ctx-body">{must_reads_html}</div>
</div>"""


def _sidebar_archive(entries: list[dict]) -> str:
    if not entries:
        return ""
    items = []
    for e in entries[:20]:
        href = html.escape(e["href"])
        label = html.escape(e["label"])
        date = html.escape(e["date"])
        items.append(
            f'<li><a href="{href}"><span class="arc-label">{label}</span>'
            f'<span class="arc-date">{date}</span></a></li>'
        )
    return f"""<div class="sidebar-card">
  <div class="sidebar-title">Archive</div>
  <ul class="archive-list">{"".join(items)}</ul>
</div>"""


# ---------------------------------------------------------------------------
# Full page template
# ---------------------------------------------------------------------------

def _page_html(
    *,
    title: str,
    human_date: str,
    is_index: bool,
    header_extra: str,
    main_content: str,
    sidebar_content: str,
    paper_json: str = "{}",
) -> str:
    back = '' if is_index else '<a class="back-link" href="../index.html">← All reports</a>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>{_CSS}</style>
</head>
<body>
<header class="site-header">
  <div class="header-inner">
    <div class="site-brand">
      <span class="site-name">Research Digest</span>
      <span class="site-tagline">Galaxy clusters · Galaxies · Lensing · Dark matter</span>
    </div>
    <div class="header-right">
      <span class="date-label">{html.escape(human_date)}</span>
      {header_extra}
    </div>
  </div>
</header>
<div class="page-wrap">
  <main class="main-col">
    {back}
    {main_content}
  </main>
  <aside class="sidebar">
    {sidebar_content}
  </aside>
</div>

<!-- Paper detail modal -->
<div id="paper-modal" class="modal-overlay" hidden>
  <div class="modal-box">
    <button class="modal-close" aria-label="Close">✕</button>
    <div class="modal-inner" id="modal-inner"></div>
  </div>
</div>

<script>
const PAPERS = {paper_json};

function esc(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function openModal(pid) {{
  const p = PAPERS[pid]; if (!p) return;
  let h = '';
  if (p.figSrc) {{
    h += '<div class="modal-fig"><img src="'+esc(p.figSrc)+'" alt="Figure"></div>';
    if (p.figCap) h += '<div class="modal-fig-cap">'+esc(p.figCap)+'</div>';
  }}
  h += '<div class="modal-pills">'+p.pills+'</div>';
  h += '<h2 class="modal-title"><a href="'+esc(p.url)+'" target="_blank" rel="noopener">'+esc(p.title)+'</a></h2>';
  let meta = esc(p.authors);
  if (p.date) meta += ' &middot; '+esc(p.date);
  if (p.cite) meta += ' &middot; '+Number(p.cite).toLocaleString()+' citations';
  h += '<div class="modal-meta">'+meta+'</div>';
  h += '<div class="modal-section-title">Abstract</div>';
  h += '<div class="modal-abstract">'+esc(p.abstract)+'</div>';
  if (p.analysisHtml) {{
    h += '<div class="modal-section-title">AI Summary</div>';
    h += '<div class="modal-analysis">'+p.analysisHtml+'</div>';
  }}
  if (p.related && p.related.length) {{
    h += '<div class="modal-related"><div class="modal-section-title">Related work</div><ul>';
    p.related.forEach(r => {{
      h += '<li><a href="'+esc(r.url||'#')+'" target="_blank" rel="noopener">'+esc(r.title)+'</a>';
      let m = [r.authors, r.year].filter(Boolean).join(', ');
      if (m) h += ' <span>— '+esc(m)+'</span>';
      h += '</li>';
    }});
    h += '</ul></div>';
  }}
  h += '<div class="modal-footer"><a class="modal-arxiv-btn" href="'+esc(p.url)+'" target="_blank" rel="noopener">Open on arXiv ↗</a></div>';
  document.getElementById('modal-inner').innerHTML = h;
  document.getElementById('paper-modal').hidden = false;
  document.body.style.overflow = 'hidden';
}}

function closeModal() {{
  document.getElementById('paper-modal').hidden = true;
  document.body.style.overflow = '';
}}

document.querySelectorAll('.paper-card[data-pid]').forEach(card => {{
  card.addEventListener('click', e => {{
    if (e.target.closest('a')) return;
    openModal(card.dataset.pid);
  }});
}});

document.getElementById('paper-modal').addEventListener('click', e => {{
  if (e.target === e.currentTarget) closeModal();
}});
document.querySelector('.modal-close').addEventListener('click', closeModal);
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Rich page builder (shared by index and archive pages)
# ---------------------------------------------------------------------------

def _build_rich_page(
    *,
    papers: list[dict],
    digest_md: str,
    date_str: str,
    human_date: str,
    is_index: bool,
    pdf_href: str | None = None,
    video_href: str | None = None,
    archive_entries: list[dict] | None = None,
    fig_prefix: str = "figures",
) -> str:
    # Assign stable pids to every paper so cards and modal can reference each other
    for i, p in enumerate(papers):
        p["_pid"] = f"p{i}"

    briefing = _extract_briefing_block(digest_md)

    # Section extraction
    exec_text  = _extract_section(
        briefing,
        r"(?:executive\s+(?:overview|summary)|what\s+is\s+new.*worth\s+noticing)",
    )
    context_text = _extract_section(digest_md, r"recent\s+context.*")
    open_q_text = _extract_section(briefing, r"open\s+questions.*|future\s+directions")
    must_read_text = _extract_section(
        briefing,
        r"(?:papers\s+to\s+read|most\s+interesting\s+papers).*",
    )

    # Group papers by primary topic
    by_topic: dict[str, list[dict]] = {t["key"]: [] for t in _TOPICS}
    for p in papers:
        key = p.get("_topic") or ""
        if key in by_topic:
            by_topic[key].append(p)
        else:
            # assign to first matching label
            label = (p.get("_topic_label") or "").lower()
            placed = False
            for t in _TOPICS:
                if t["label"].lower() in label:
                    by_topic[t["key"]].append(p)
                    placed = True
                    break
            if not placed:
                by_topic[_TOPICS[0]["key"]].append(p)

    # Main content
    parts: list[str] = []

    # Report topbar (for archive pages)
    if not is_index and (pdf_href or video_href):
        action_links = ""
        if pdf_href:
            action_links += f'<a class="action-pill" href="{html.escape(pdf_href)}">PDF</a>'
        if video_href:
            action_links += f'<a class="action-pill" href="{html.escape(video_href)}">Video</a>'
        parts.append(f'<div class="report-topbar"><span></span><div class="report-actions">{action_links}</div></div>')

    # Executive overview
    if exec_text:
        overview_html = _md_to_html(exec_text)
        parts.append(f"""<div class="overview-card">
  <div class="eyebrow">Today's Overview — {html.escape(human_date)}</div>
  <div class="overview-body">{overview_html}</div>
</div>""")

    # Topic sections
    for topic in _TOPICS:
        synth_text = _extract_section(briefing, topic["section_re"])
        synth_html = _md_to_html(synth_text) if synth_text else ""
        topic_papers = by_topic[topic["key"]]
        parts.append(_topic_block(topic, topic_papers, synth_html, fig_prefix=fig_prefix))

    # Open questions
    if open_q_text:
        oq_html = _md_to_html(open_q_text)
        parts.append(f"""<div class="open-qs-card">
  <div class="eyebrow">Open Questions &amp; Follow-ups</div>
  <div class="open-qs-body">{oq_html}</div>
</div>""")

    # Sidebar
    sidebar_parts: list[str] = []

    if context_text:
        sidebar_parts.append(_sidebar_context(_md_to_html(context_text)))

    if must_read_text:
        sidebar_parts.append(_sidebar_must_reads(_md_to_html(must_read_text)))

    if archive_entries:
        sidebar_parts.append(_sidebar_archive(archive_entries))

    # Build PAPERS JSON for the modal (one entry per paper with a pid)
    paper_data: dict[str, dict] = {}
    for p in papers:
        pid = p.get("_pid")
        if not pid:
            continue
        fig_path, fig_caption = _figure_site_path(p)
        fig_src = ""
        if fig_path:
            key = _safe_name(p.get("id") or p.get("title", ""))
            fig_src = f"{fig_prefix}/{key}/figure_1.png"
        s2 = p.get("semantic_scholar") or {}
        raw_analysis = p.get("analysis") or {}
        if isinstance(raw_analysis, dict):
            ah: list[str] = []
            if raw_analysis.get("one_sentence_summary"):
                ah.append(f'<p><strong>{html.escape(raw_analysis["one_sentence_summary"])}</strong></p>')
            for _key, _lbl in [("methods", "Methods"), ("novelty_claim", "Novelty"), ("limitations", "Limitations")]:
                if raw_analysis.get(_key):
                    ah.append(f'<p><em>{_lbl}:</em> {html.escape(raw_analysis[_key])}</p>')
            analysis_html = "".join(ah)
        else:
            analysis_html = f'<p>{html.escape(str(raw_analysis))}</p>' if raw_analysis else ""
        paper_data[pid] = {
            "title":        p.get("title") or "",
            "url":          _arxiv_url(p),
            "authors":      _format_authors(p.get("authors") or []),
            "date":         _paper_date(p),
            "cite":         s2.get("citationCount"),
            "pills":        _topic_pills(p),
            "abstract":     (p.get("summary") or "").strip(),
            "analysisHtml": analysis_html,
            "figSrc":       fig_src,
            "figCap":       fig_caption,
            "related":      p.get("related_papers") or [],
        }
    paper_json = json.dumps(paper_data, ensure_ascii=False)

    # Header pills
    header_extra = ""
    if is_index:
        if pdf_href:
            header_extra += f'<a class="header-pill" href="{html.escape(pdf_href)}">PDF</a>'
        if video_href:
            header_extra += f'<a class="header-pill" href="{html.escape(video_href)}">Video</a>'

    return _page_html(
        title=f"Research Digest — {human_date}",
        human_date=human_date,
        is_index=is_index,
        header_extra=header_extra,
        main_content="\n".join(parts),
        sidebar_content="\n".join(sidebar_parts),
        paper_json=paper_json,
    )


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _report_date_from_stem(stem: str) -> str:
    return stem.removeprefix("daily-")


def _human_date(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        return date_str


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_pages_site(
    reports_dir: Path = Path("data/reports"),
    cache_dir: Path = Path("data/cache"),
    videos_dir: Path = Path("data/videos"),
    site_dir: Path = Path("docs"),
    include_videos: bool = False,
) -> Path:
    md_paths    = sorted(reports_dir.glob("daily-*.md"), reverse=True)
    pdf_by_stem = {p.stem: p for p in reports_dir.glob("daily-*.pdf")}
    vid_by_stem = (
        {p.stem: p for p in videos_dir.glob("daily-*.mp4")}
        if include_videos else {}
    )

    site_dir.mkdir(parents=True, exist_ok=True)
    reports_out = site_dir / "reports"
    videos_out  = site_dir / "videos"
    reports_out.mkdir(parents=True, exist_ok=True)
    if include_videos:
        videos_out.mkdir(parents=True, exist_ok=True)
    _FIGURES_SITE.mkdir(parents=True, exist_ok=True)

    # Clear old artifacts
    for f in reports_out.glob("*.pdf"):  f.unlink()
    for f in reports_out.glob("*.html"): f.unlink()
    if videos_out.exists():
        for f in videos_out.glob("*.mp4"):
            f.unlink()
    latest_site_video = site_dir / "latest.mp4"
    if latest_site_video.exists():
        latest_site_video.unlink()

    # Copy binary assets
    for stem, p in pdf_by_stem.items():
        shutil.copy2(p, reports_out / p.name)
    for stem, p in vid_by_stem.items():
        shutil.copy2(p, videos_out / p.name)

    latest_pdf   = reports_dir / "latest.pdf"
    latest_video = videos_dir  / "latest.mp4"
    if latest_pdf.exists():   shutil.copy2(latest_pdf,   site_dir / "latest.pdf")
    if include_videos and latest_video.exists():
        shutil.copy2(latest_video, site_dir / "latest.mp4")

    # Build archive entry list (for sidebar)
    archive_entries: list[dict] = []
    for md_path in md_paths:
        stem  = md_path.stem
        ds    = _report_date_from_stem(stem)
        hd    = _human_date(ds)
        archive_entries.append({
            "href":  f"reports/{stem}.html",
            "label": hd,
            "date":  ds,
        })

    # Build per-report HTML pages
    for md_path in md_paths:
        stem     = md_path.stem
        date_str = _report_date_from_stem(stem)
        hdate    = _human_date(date_str)

        # Load cache JSON if available
        cache_path = cache_dir / f"{stem}.json"
        papers: list[dict] = []
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text())
                papers = data.get("selected") or []
            except Exception:
                pass

        digest_md = md_path.read_text(encoding="utf-8")

        # Relative hrefs from reports/ subdirectory
        pdf_href   = f"{stem}.pdf"   if stem in pdf_by_stem else None
        video_href = f"../videos/{stem}.mp4" if stem in vid_by_stem else None

        page_html = _build_rich_page(
            papers=papers,
            digest_md=digest_md,
            date_str=date_str,
            human_date=hdate,
            is_index=False,
            pdf_href=pdf_href,
            video_href=video_href,
            archive_entries=None,
            fig_prefix="../figures",
        )
        (reports_out / f"{stem}.html").write_text(page_html, encoding="utf-8")

    # Build index (latest report + archive sidebar)
    index_html: str
    if md_paths:
        latest_md   = md_paths[0]
        stem        = latest_md.stem
        date_str    = _report_date_from_stem(stem)
        hdate       = _human_date(date_str)

        cache_path = cache_dir / f"{stem}.json"
        papers = []
        if cache_path.exists():
            try:
                papers = json.loads(cache_path.read_text()).get("selected") or []
            except Exception:
                pass

        pdf_href_idx   = f"reports/{stem}.pdf"   if stem in pdf_by_stem else None
        video_href_idx = f"videos/{stem}.mp4"    if stem in vid_by_stem else None

        index_html = _build_rich_page(
            papers=papers,
            digest_md=latest_md.read_text(encoding="utf-8"),
            date_str=date_str,
            human_date=hdate,
            is_index=True,
            pdf_href=pdf_href_idx,
            video_href=video_href_idx,
            archive_entries=archive_entries,
            fig_prefix="figures",
        )
        # Also write latest.html
        (site_dir / "latest.html").write_text(index_html, encoding="utf-8")
    else:
        index_html = _page_html(
            title="Research Digest",
            human_date="",
            is_index=True,
            header_extra="",
            main_content='<p style="color:var(--muted);padding:3rem 0">No reports published yet.</p>',
            sidebar_content="",
        )

    (site_dir / "index.html").write_text(index_html, encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    return site_dir / "index.html"
