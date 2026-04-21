import json
from datetime import date, datetime, timezone
from pathlib import Path

import typer
from rich import print

from app.config import DEFAULT_RESEARCH_TOPICS
from app.digest import synthesize_research_digest
from app.figures import extract_key_figures
from app.fetch_arxiv import fetch_recent_arxiv
from app.enrich_s2 import enrich_title
from app.page_summaries import summarize_pdf_pages
from app.pdf_report import build_daily_pdf_report
from app.select_papers import select_top_papers
from app.site_pages import build_pages_site
from app.summarize import summarize_with_ollama

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
            item["analysis"] = summarize_with_ollama(paper["title"], paper["summary"])

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
    days: int = typer.Option(3, help="Only include papers from the last N days."),
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
):
    """
    Scan arXiv once for each configured topic (clusters, galaxies, lensing, dark matter),
    dedupe, optionally enrich, then write a dated report with an LLM research digest.
    """
    collected: list[dict] = []
    for topic_key, query in DEFAULT_RESEARCH_TOPICS:
        batch = fetch_recent_arxiv(
            query=query,
            category=category,
            days=days,
            limit=min(pool_per_topic, 25),
        )
        for p in batch:
            p = dict(p)
            p["_topic"] = topic_key
            p["_topic_label"] = _topic_label(topic_key)
            collected.append(p)

    merged = _sort_by_published(_dedupe_papers(collected))
    if max_pool > 0 and len(merged) > max_pool:
        merged = merged[:max_pool]

    pool_for_selection = [dict(p) for p in merged]
    k = max(1, present)
    selected_pool, selection_note = select_top_papers(pool_for_selection, k=k)

    enriched: list[dict] = []
    for paper in selected_pool:
        item = dict(paper)
        if enrich:
            try:
                item["semantic_scholar"] = enrich_title(paper["title"])
            except Exception as e:
                item["semantic_scholar_error"] = str(e)
        if per_paper:
            item["analysis"] = summarize_with_ollama(paper["title"], paper["summary"])
        enriched.append(item)

    digest_md = ""
    if digest:
        try:
            digest_md = synthesize_research_digest(enriched)
        except Exception as e:
            digest_md = f"_Digest generation failed: {e}_"

    today = date.today().isoformat()
    Path("data/cache").mkdir(parents=True, exist_ok=True)
    Path("data/reports").mkdir(parents=True, exist_ok=True)

    cache_path = Path(f"data/cache/daily-{today}.json")
    report_path = Path(f"data/reports/daily-{today}.md")
    report_pdf_path = Path(f"data/reports/daily-{today}.pdf")

    payload = {
        "pool_count": len(pool_for_selection),
        "selected_count": len(enriched),
        "present_requested": k,
        "selection_note": selection_note,
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
        if digest_md:
            f.write("## Research briefing (synthesized)\n\n")
            f.write(digest_md)
            f.write("\n\n---\n\n")
        f.write("## Catalog (selected)\n\n")
        for i, p in enumerate(enriched, 1):
            f.write(f"### {i}. {p['title']}\n")
            f.write(f"- **Focus:** {p.get('_topic_label', '')}\n")
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

    figure_map: dict[str, list[str]] = {}
    page_summary_map: dict[str, list[dict]] = {}
    if pdf:
        cache_pdf_dir = Path("data/cache/pdfs")
        cache_fig_dir = Path("data/cache/figures")
        cache_pdf_dir.mkdir(parents=True, exist_ok=True)
        cache_fig_dir.mkdir(parents=True, exist_ok=True)

        for paper in enriched:
            paper_id = paper.get("id") or paper.get("title", "")
            if not paper_id:
                continue
            try:
                figure_map[paper_id] = extract_key_figures(
                    paper,
                    cache_pdf_dir=cache_pdf_dir,
                    cache_fig_dir=cache_fig_dir,
                    max_figures=max(0, figures_per_paper),
                    use_gemini=gemini_figures,
                )
            except Exception:
                figure_map[paper_id] = []
            if page_summaries:
                try:
                    page_summary_map[paper_id] = summarize_pdf_pages(
                        paper,
                        cache_pdf_dir=cache_pdf_dir,
                        max_pages=max(0, max_pages_per_paper),
                    )
                except Exception:
                    page_summary_map[paper_id] = []

        build_daily_pdf_report(
            out_path=report_pdf_path,
            report_date=today,
            digest_md=digest_md,
            papers=enriched,
            figure_map=figure_map,
            page_summary_map=page_summary_map,
        )

    # Overwrite stable paths for quick access / cron consumers
    with open("data/cache/latest.json", "w") as f:
        json.dump(payload, f, indent=2)
    with open("data/reports/latest.md", "w") as f:
        f.write(report_path.read_text())
    if pdf and report_pdf_path.exists():
        with open("data/reports/latest.pdf", "wb") as f:
            f.write(report_pdf_path.read_bytes())
        build_pages_site()

    print(
        f"[green]Daily: pool {len(pool_for_selection)} → presenting {len(enriched)}[/green]"
    )
    print(f"[cyan]JSON:[/cyan] {cache_path}")
    print(f"[cyan]Report:[/cyan] {report_path}")
    if pdf and report_pdf_path.exists():
        print(f"[cyan]PDF:[/cyan] {report_pdf_path}")
        print("[cyan]Site:[/cyan] docs/index.html")
    print("[cyan]Also:[/cyan] data/cache/latest.json, data/reports/latest.md")


if __name__ == "__main__":
    app()
