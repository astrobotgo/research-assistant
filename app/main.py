import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import typer
from rich import print
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

_console = Console()

from app.agents import COPERNICUS, PTOLEMY
from app.config import TOPIC_CONFIGS, WATCHLIST
from app.context import build_research_context, update_field_state
from app.digest import synthesize_research_digest
from app.figures import extract_key_figures
from app.fetch_arxiv import fetch_recent_arxiv
from app.enrich_s2 import enrich_title, get_related_papers
from app.page_summaries import summarize_pdf_pages
from app.pdf_report import build_daily_pdf_report
from app.select_papers import select_top_papers
from app.site_pages import build_pages_site
from app.summarize import summarize_paper, enrich_background
from app.video_report import build_narrated_video

app = typer.Typer()


def _topic_label(key: str) -> str:
    return key.replace("_", " ")


def _dedupe_papers(papers: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for p in papers:
        pid = p.get("id") or p.get("title", "")
        if not pid:
            continue
        label = p.get("_topic_label") or _topic_label(p.get("_topic", ""))
        if pid not in by_id:
            by_id[pid] = dict(p)
            by_id[pid]["_topic_labels"] = [label] if label else []
            order.append(pid)
        elif label and label not in by_id[pid]["_topic_labels"]:
            by_id[pid]["_topic_labels"].append(label)
    out: list[dict] = []
    for pid in order:
        item = by_id[pid]
        labels = item.get("_topic_labels") or []
        item["_topic_label"] = ", ".join(labels) if labels else ""
        item.pop("_topic_labels", None)
        out.append(item)
    return out


def _sort_by_published(papers: list[dict]) -> list[dict]:
    def key(p: dict) -> datetime:
        raw = p.get("published") or ""
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    return sorted(papers, key=key, reverse=True)


def _auto_widen_days(requested_days: int) -> int:
    """
    If the last successful run was more than `requested_days` ago, extend the
    window so the gap is covered. This handles weekends and missed runs.
    """
    cache_files = sorted(Path("data/cache").glob("daily-*.json"))
    if not cache_files:
        return requested_days
    latest_stem = cache_files[-1].stem  # e.g. "daily-2026-04-21"
    try:
        last_run = date.fromisoformat(latest_stem.replace("daily-", ""))
    except ValueError:
        return requested_days
    gap = (date.today() - last_run).days
    if gap > requested_days:
        widened = gap + 1
        print(
            f"[yellow]Auto-widening --days from {requested_days} to {widened} "
            f"(last run was {gap} day(s) ago)[/yellow]"
        )
        return widened
    return requested_days


def _matches_watchlist(paper: dict, watchlist: dict) -> str:
    """Return the matched watchlist term, or '' if no match."""
    surveys = [s.lower() for s in (watchlist.get("surveys") or [])]
    authors = [a.lower() for a in (watchlist.get("authors") or [])]
    text = " ".join([
        paper.get("title") or "",
        paper.get("summary") or "",
        " ".join(paper.get("authors") or []),
    ]).lower()
    for term in surveys + authors:
        if term and term in text:
            return term
    return ""


def _apply_watchlist(pool: list[dict], selected: list[dict], watchlist: dict) -> list[dict]:
    """
    Force-include watchlisted papers that aren't already in the selection.
    Appends them (up to 3 extras) without displacing existing picks.
    """
    if not watchlist.get("surveys") and not watchlist.get("authors"):
        return selected

    selected_ids = {p.get("id") or p.get("title") for p in selected}
    extras: list[dict] = []
    for p in pool:
        pid = p.get("id") or p.get("title")
        if pid in selected_ids:
            continue
        match = _matches_watchlist(p, watchlist)
        if match:
            p = dict(p)
            p["_watchlisted"] = match
            extras.append(p)
            selected_ids.add(pid)
            if len(extras) >= 3:
                break

    if extras:
        labels = [f"'{e['_watchlisted']}'" for e in extras]
        print(f"[cyan]Watchlist:[/cyan] force-including {len(extras)} paper(s) matching {', '.join(labels)}")

    return selected + extras


def _append_open_questions(today: str, digest_md: str) -> None:
    """Extract the open-questions section from today's digest and append to the tracker."""
    m = re.search(
        r"## Open questions.*?\n(.*?)(?=\n## |\Z)",
        digest_md,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return
    questions_text = m.group(1).strip()
    if not questions_text:
        return

    oq_path = Path("data/open_questions.md")
    existing = oq_path.read_text() if oq_path.exists() else ""

    # Avoid duplicating the same date's entry
    if f"### {today}" in existing:
        return

    with open(oq_path, "a") as f:
        if existing and not existing.endswith("\n\n"):
            f.write("\n\n" if existing.endswith("\n") else "\n\n")
        f.write(f"### {today}\n\n{questions_text}\n")


@app.command()
def scan(
    query: str,
    category: str = "",
    days: int = 7,
    limit: int = 10,
    summarize: bool = True,
):
    papers = fetch_recent_arxiv(query=query, category=category, days=days, limit=limit)

    enriched = []
    for paper in papers:
        item = dict(paper)

        try:
            item["semantic_scholar"] = enrich_title(paper["title"])
        except Exception as e:
            item["semantic_scholar_error"] = str(e)

        if summarize:
            item["analysis"] = summarize_paper(paper["title"], paper["summary"])

        enriched.append(item)

    Path("data/cache").mkdir(parents=True, exist_ok=True)
    Path("data/reports").mkdir(parents=True, exist_ok=True)

    with open("data/cache/latest.json", "w") as f:
        json.dump(enriched, f, indent=2)

    with open("data/reports/latest.md", "w") as f:
        f.write(f"# Research Scan: {query}\n\n")
        for i, p in enumerate(enriched, 1):
            f.write(f"## {i}. {p['title']}\n")
            f.write(f"- Published: {p['published']}\n")
            f.write(f"- Authors: {', '.join(p['authors'])}\n")
            f.write(f"- Categories: {', '.join(p['categories'])}\n")
            f.write(f"- PDF: {p['pdf_url']}\n\n")
            f.write(f"### Abstract\n{p['summary']}\n\n")

            s2 = p.get("semantic_scholar")
            if s2:
                f.write("### Semantic Scholar\n")
                f.write(f"- Year: {s2.get('year', '')}\n")
                f.write(f"- Venue: {s2.get('venue', '')}\n")
                f.write(f"- Citation Count: {s2.get('citationCount', '')}\n")
                f.write(f"- Influential Citations: {s2.get('influentialCitationCount', '')}\n")
                f.write(f"- Fields of Study: {', '.join(s2.get('fieldsOfStudy', []) or [])}\n")
                f.write(f"- URL: {s2.get('url', '')}\n\n")

            analysis = p.get("analysis", {})
            if analysis:
                f.write("### Analysis\n")
                f.write(f"- Summary: {analysis.get('one_sentence_summary', '')}\n")
                f.write(f"- Methods: {analysis.get('methods', '')}\n")
                f.write(f"- Limitations: {analysis.get('limitations', '')}\n")
                f.write(f"- Novelty: {analysis.get('novelty_claim', '')}\n")
                f.write(f"- Relevance: {analysis.get('relevance_score_1_to_10', '')}\n\n")

    print(f"[green]Saved {len(enriched)} papers[/green]")
    print("[cyan]JSON:[/cyan] data/cache/latest.json")
    print("[cyan]Report:[/cyan] data/reports/latest.md")


@app.command()
def daily(
    days: int = typer.Option(3, help="Only include papers from the last N days. Auto-widens when previous runs were missed."),
    pool_per_topic: int = typer.Option(
        8,
        help="Fetch up to this many papers per topic before dedupe (cap 25; ~20–30 total typical).",
    ),
    present: int = typer.Option(
        10,
        help="After widening the pool, the model picks this many papers for the digest/PDF/catalog.",
    ),
    max_pool: int = typer.Option(
        30,
        help="Cap pool size before selection (newest first); 0 = no cap.",
    ),
    category: str = typer.Option(
        "",
        help='Optional arXiv category, e.g. "astro-ph.CO" (empty = all categories).',
    ),
    enrich: bool = typer.Option(
        True, help="Attach Semantic Scholar metadata when possible."
    ),
    per_paper: bool = typer.Option(
        False,
        help="Run Ollama on each abstract (slow); digest still runs without this.",
    ),
    digest: bool = typer.Option(
        True, help="Synthesize one cross-paper briefing via Ollama."
    ),
    pdf: bool = typer.Option(
        True, help="Generate a PDF digest with extracted paper figures."
    ),
    figures_per_paper: int = typer.Option(
        1, help="Max number of extracted figures per paper in the PDF."
    ),
    gemini_figures: bool = typer.Option(
        True,
        help="Use Gemini API to rank extracted figures (falls back automatically if unavailable).",
    ),
    page_summaries: bool = typer.Option(
        False,
        help="Generate one concise summary per PDF page and include in the PDF report.",
    ),
    max_pages_per_paper: int = typer.Option(
        0,
        help="Cap pages summarized per paper (0 means all pages).",
    ),
    video: bool = typer.Option(
        False,
        help="Opt in to generating a narrated slideshow video from the daily PDF.",
    ),
    context_days: int = typer.Option(
        7,
        help="Days of history the context agent reads when building the weekly summary.",
    ),
):
    """
    Scan arXiv once for each configured topic (clusters, galaxies, lensing, dark matter),
    dedupe, optionally enrich, then write a dated report with an LLM research digest.
    """
    days = _auto_widen_days(days)

    print(f"[cyan]{PTOLEMY.name}:[/cyan] scanning arXiv across {len(TOPIC_CONFIGS)} topics (last {days} days)...")
    collected: list[dict] = []
    for topic in TOPIC_CONFIGS:
        label = topic.get("label", topic["key"])
        print(f"  [dim]fetching:[/dim] {label}")
        batch = fetch_recent_arxiv(
            query=topic["arxiv_query"],
            category=category,
            days=days,
            limit=min(pool_per_topic, 25),
            include_any=tuple(topic.get("include_any", ())),
            exclude_any=tuple(topic.get("exclude_any", ())),
        )
        for p in batch:
            p = dict(p)
            p["_topic"] = topic["key"]
            p["_topic_label"] = topic.get("label", _topic_label(topic["key"]))
            collected.append(p)
        print(f"  [dim]  → {len(batch)} papers[/dim]")

    merged = _sort_by_published(_dedupe_papers(collected))
    if max_pool > 0 and len(merged) > max_pool:
        merged = merged[:max_pool]
    print(f"[cyan]{PTOLEMY.name}:[/cyan] {len(collected)} collected, {len(merged)} unique after deduplication")

    print(f"[cyan]{COPERNICUS.name}:[/cyan] building historical context from recent briefings...")
    try:
        research_context = build_research_context(days_back=context_days)
    except Exception as e:
        print(f"[yellow]{COPERNICUS.name} skipped:[/yellow] {e}")
        research_context = {"summary": "", "covered_ids": set(), "covered_titles": []}

    pool_for_selection = [dict(p) for p in merged]
    k = max(1, present)
    print(f"[cyan]{PTOLEMY.name}:[/cyan] selecting top {k} papers from pool of {len(pool_for_selection)}...")
    selected_pool, selection_note = select_top_papers(
        pool_for_selection,
        k=k,
        covered_ids=research_context.get("covered_ids") or set(),
        selection_context=research_context.get("selection_brief") or {},
    )
    print(f"[cyan]{PTOLEMY.name}:[/cyan] selected {len(selected_pool)} papers")

    # Force-include watchlisted papers not already chosen
    selected_pool = _apply_watchlist(pool_for_selection, selected_pool, WATCHLIST)

    # Parallel enrichment (Ptolemy): S2 lookup + optional per-paper summarization
    # Copernicus also fetches related papers via S2 recommendations for each paper.
    def _enrich_one(paper: dict) -> dict:
        item = dict(paper)
        if enrich:
            try:
                s2 = enrich_title(paper["title"])
                item["semantic_scholar"] = s2
                # Copernicus: fetch related papers using the S2 paper ID
                if s2 and s2.get("paperId"):
                    item["related_papers"] = get_related_papers(s2["paperId"], n=3)
            except Exception as e:
                item["semantic_scholar_error"] = str(e)
        if per_paper:
            try:
                item["analysis"] = summarize_paper(paper["title"], paper["summary"])
            except Exception:
                pass
        try:
            item["background"] = enrich_background(paper["title"], paper.get("summary", ""))
        except Exception:
            pass
        return item

    enriched: list[dict] = []
    print(f"[cyan]{PTOLEMY.name}:[/cyan] enriching {len(selected_pool)} papers (Semantic Scholar + related)...")
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(), console=_console) as progress:
        task = progress.add_task(f"{PTOLEMY.name}: enriching", total=len(selected_pool))
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(_enrich_one, p): i for i, p in enumerate(selected_pool)}
            results: dict[int, dict] = {}
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
                progress.advance(task)
    enriched = [results[i] for i in range(len(selected_pool))]

    # Compute page summaries before digest so the LLM has full paper content.
    early_page_summary_map: dict[str, list[dict]] = {}
    if page_summaries:
        cache_pdf_dir_early = Path("data/cache/pdfs")
        cache_pdf_dir_early.mkdir(parents=True, exist_ok=True)

        def _summarize_one(paper: dict) -> tuple[str, list[dict]]:
            paper_id = paper.get("id") or paper.get("title", "")
            if not paper_id:
                return paper_id, []
            try:
                pages = summarize_pdf_pages(
                    paper,
                    cache_pdf_dir=cache_pdf_dir_early,
                    max_pages=max(0, max_pages_per_paper),
                )
            except Exception:
                pages = []
            return paper_id, pages

        print(f"[cyan]{PTOLEMY.name}:[/cyan] downloading and summarizing {len(enriched)} PDFs...")
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(), console=_console) as progress:
            task = progress.add_task(f"{PTOLEMY.name}: reading papers", total=len(enriched))
            with ThreadPoolExecutor(max_workers=4) as executor:
                sum_futures = [executor.submit(_summarize_one, p) for p in enriched]
                for future in as_completed(sum_futures):
                    pid, pages = future.result()
                    if pid:
                        early_page_summary_map[pid] = pages
                    progress.advance(task)

    digest_md = ""
    if digest:
        print(f"[cyan]{COPERNICUS.name}:[/cyan] synthesizing research digest...")
        try:
            digest_md = synthesize_research_digest(
                enriched,
                context=research_context.get("summary") or "",
                page_summary_map=early_page_summary_map or None,
            )
            print(f"[green]{COPERNICUS.name}:[/green] digest complete")
        except Exception as e:
            digest_md = f"_Digest generation failed: {e}_"
            print(f"[red]{COPERNICUS.name}:[/red] digest failed: {e}")

    today = date.today().isoformat()
    Path("data/cache").mkdir(parents=True, exist_ok=True)
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    if video:
        Path("data/videos").mkdir(parents=True, exist_ok=True)

    cache_path = Path(f"data/cache/daily-{today}.json")
    report_path = Path(f"data/reports/daily-{today}.md")
    report_pdf_path = Path(f"data/reports/daily-{today}.pdf")
    report_video_path = Path(f"data/videos/daily-{today}.mp4")

    context_summary = research_context.get("summary") or ""
    payload = {
        "agents": {
            "discovery": PTOLEMY.as_dict(),
            "synthesis": COPERNICUS.as_dict(),
        },
        "pool_count": len(pool_for_selection),
        "selected_count": len(enriched),
        "present_requested": k,
        "selection_note": selection_note,
        "selection_brief": research_context.get("selection_brief") or {},
        "context_covered_count": len(research_context.get("covered_ids") or []),
        "pool": pool_for_selection,
        "selected": enriched,
    }
    with open(cache_path, "w") as f:
        json.dump(payload, f, indent=2)

    selected_keys = {
        p.get("id") or p.get("title") for p in selected_pool
    }
    remainder = [
        p
        for p in pool_for_selection
        if (p.get("id") or p.get("title")) not in selected_keys
    ]

    with open(report_path, "w") as f:
        f.write(f"# Daily research digest — {today}\n\n")
        f.write(
            "Topics scanned: galaxy clusters, galaxies, gravitational lensing, dark matter.\n\n"
        )
        f.write(
            f"**Pool:** {len(pool_for_selection)} recent papers gathered. "
            f"**Presenting:** {len(enriched)} chosen by the local model for this briefing.\n\n"
        )
        if selection_note:
            f.write(f"**Selection note:** {selection_note}\n\n")
        if context_summary:
            f.write(context_summary)
            f.write("\n\n---\n\n")
        if digest_md:
            f.write("## Research briefing (synthesized)\n\n")
            f.write(digest_md)
            f.write("\n\n---\n\n")
        f.write("## Catalog (selected)\n\n")
        for i, p in enumerate(enriched, 1):
            f.write(f"### {i}. {p['title']}\n")
            f.write(f"- **Focus:** {p.get('_topic_label', '')}\n")
            if p.get("_selection_reason"):
                f.write(f"- **Why selected:** {p['_selection_reason']}\n")
            if p.get("_watchlisted"):
                f.write(f"- **Watchlisted:** {p['_watchlisted']}\n")
            f.write(f"- Published: {p['published']}\n")
            f.write(f"- Authors: {', '.join(p['authors'])}\n")
            f.write(f"- Categories: {', '.join(p['categories'])}\n")
            f.write(f"- PDF: {p['pdf_url']}\n\n")
            f.write(f"#### Abstract\n{p['summary']}\n\n")
            s2 = p.get("semantic_scholar")
            if s2:
                f.write("#### Semantic Scholar\n")
                f.write(f"- Year: {s2.get('year', '')}\n")
                f.write(f"- Venue: {s2.get('venue', '')}\n")
                f.write(f"- Citation Count: {s2.get('citationCount', '')}\n")
                f.write(
                    f"- Influential Citations: {s2.get('influentialCitationCount', '')}\n"
                )
                f.write(
                    f"- Fields of Study: {', '.join(s2.get('fieldsOfStudy', []) or [])}\n"
                )
                f.write(f"- URL: {s2.get('url', '')}\n\n")
            analysis = p.get("analysis", {})
            if analysis:
                f.write("#### Per-paper analysis\n")
                f.write(f"- Summary: {analysis.get('one_sentence_summary', '')}\n")
                f.write(f"- Methods: {analysis.get('methods', '')}\n")
                f.write(f"- Limitations: {analysis.get('limitations', '')}\n")
                f.write(f"- Novelty: {analysis.get('novelty_claim', '')}\n")
                f.write(f"- Relevance: {analysis.get('relevance_score_1_to_10', '')}\n\n")

        if remainder:
            f.write("---\n\n## Other candidates (not in top selection)\n\n")
            for p in remainder:
                f.write(f"- **{p['title']}** — {p.get('_topic_label', '')} — {p.get('pdf_url', '')}\n")

    # Persist open questions and update the long-term field state document
    if digest_md:
        try:
            _append_open_questions(today, digest_md)
        except Exception:
            pass
        try:
            print(f"[cyan]{COPERNICUS.name}:[/cyan] updating long-term field state...")
            update_field_state(today, digest_md)
            print(f"[green]{COPERNICUS.name}:[/green] field state updated")
        except Exception as e:
            print(f"[yellow]{COPERNICUS.name}:[/yellow] field state update skipped: {e}")

    # Parallel figure extraction + optional page summaries
    figure_map: dict[str, list[dict]] = {}
    page_summary_map: dict[str, list[dict]] = early_page_summary_map
    if pdf:
        cache_pdf_dir = Path("data/cache/pdfs")
        cache_fig_dir = Path("data/cache/figures")
        cache_pdf_dir.mkdir(parents=True, exist_ok=True)
        cache_fig_dir.mkdir(parents=True, exist_ok=True)

        def _process_paper_figures(paper: dict) -> tuple[str, list[dict], list[dict]]:
            paper_id = paper.get("id") or paper.get("title", "")
            if not paper_id:
                return paper_id, [], []
            figs: list[dict] = []
            pages: list[dict] = []
            try:
                figs = extract_key_figures(
                    paper,
                    cache_pdf_dir=cache_pdf_dir,
                    cache_fig_dir=cache_fig_dir,
                    max_figures=max(0, figures_per_paper),
                    use_gemini=gemini_figures,
                )
            except Exception:
                pass
            if page_summaries and paper_id not in early_page_summary_map:
                try:
                    pages = summarize_pdf_pages(
                        paper,
                        cache_pdf_dir=cache_pdf_dir,
                        max_pages=max(0, max_pages_per_paper),
                    )
                except Exception:
                    pass
            return paper_id, figs, pages

        print(f"[cyan]{PTOLEMY.name}:[/cyan] extracting figures from {len(enriched)} papers...")
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(), console=_console) as progress:
            task = progress.add_task(f"{PTOLEMY.name}: extracting figures", total=len(enriched))
            with ThreadPoolExecutor(max_workers=4) as executor:
                fig_futures = [executor.submit(_process_paper_figures, p) for p in enriched]
                for future in as_completed(fig_futures):
                    paper_id, figs, pages = future.result()
                    if paper_id:
                        figure_map[paper_id] = figs
                        if pages:
                            page_summary_map[paper_id] = pages
                    progress.advance(task)

        build_daily_pdf_report(
            out_path=report_pdf_path,
            report_date=today,
            digest_md=digest_md,
            papers=enriched,
            figure_map=figure_map,
            page_summary_map=page_summary_map,
        )
        if video and report_pdf_path.exists():
            try:
                build_narrated_video(
                    pdf_path=report_pdf_path,
                    out_path=report_video_path,
                    title=f"Daily Research Digest {today}",
                )
            except Exception as e:
                print(f"[yellow]Video skipped:[/yellow] {e}")

    # Overwrite stable paths for quick access / cron consumers
    with open("data/cache/latest.json", "w") as f:
        json.dump(payload, f, indent=2)
    with open("data/reports/latest.md", "w") as f:
        f.write(report_path.read_text())
    if pdf and report_pdf_path.exists():
        with open("data/reports/latest.pdf", "wb") as f:
            f.write(report_pdf_path.read_bytes())
        if video and report_video_path.exists():
            with open("data/videos/latest.mp4", "wb") as f:
                f.write(report_video_path.read_bytes())
        build_pages_site(include_videos=video)

    print(
        f"[green]{PTOLEMY.name}: pool {len(pool_for_selection)} -> presenting {len(enriched)}[/green]"
    )
    print(f"[cyan]JSON:[/cyan] {cache_path}")
    print(f"[cyan]Report:[/cyan] {report_path}")
    if pdf and report_pdf_path.exists():
        print(f"[cyan]PDF:[/cyan] {report_pdf_path}")
        if video and report_video_path.exists():
            print(f"[cyan]Video:[/cyan] {report_video_path}")
        print("[cyan]Site:[/cyan] docs/index.html")
    print("[cyan]Also:[/cyan] data/cache/latest.json, data/reports/latest.md")


if __name__ == "__main__":
    app()
