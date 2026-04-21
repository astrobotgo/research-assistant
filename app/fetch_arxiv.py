import time
import httpx
import feedparser
from datetime import datetime, timezone, timedelta

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_ATOM = "https://rss.arxiv.org/atom"

HEADERS = {
    "User-Agent": "research-assistant/0.1"
}

def _parse_entries(entries, days: int, query: str = ""):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    papers = []
    q = query.lower().strip()

    for entry in entries:
        published_raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
        if not published_raw:
            continue

        try:
            if published_raw.endswith("Z"):
                published = datetime.strptime(
                    published_raw, "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
            else:
                published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if published < cutoff:
            continue

        title = " ".join(getattr(entry, "title", "").split())
        summary = " ".join(getattr(entry, "summary", "").split())

        if q and q not in title.lower() and q not in summary.lower():
            continue

        pdf_url = None
        for link in getattr(entry, "links", []):
            href = getattr(link, "href", "")
            ltype = getattr(link, "type", "")
            if ltype == "application/pdf" or href.endswith(".pdf"):
                pdf_url = href
                break

        papers.append({
            "id": getattr(entry, "id", ""),
            "title": title,
            "summary": summary,
            "published": published.isoformat(),
            "updated": getattr(entry, "updated", published.isoformat()),
            "authors": [a.name for a in getattr(entry, "authors", [])],
            "pdf_url": pdf_url,
            "categories": [t.term for t in getattr(entry, "tags", [])],
        })

    return papers

def fetch_recent_arxiv(query: str, category: str = "", days: int = 7, limit: int = 10):
    search = f'all:"{query}"'
    if category:
        search += f"+AND+cat:{category}"

    params = {
        "search_query": search,
        "start": 0,
        "max_results": min(limit, 25),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    delays = [3, 6, 12]

    for i, delay in enumerate(delays, start=1):
        try:
            r = httpx.get(ARXIV_API, params=params, headers=HEADERS, timeout=90.0)
            if r.status_code == 429:
                if i < len(delays):
                    time.sleep(delay)
                    continue
                break
            r.raise_for_status()
            feed = feedparser.parse(r.text)
            # Keep a local keyword guard to avoid false positives from broad arXiv matches.
            return _parse_entries(feed.entries, days=days, query=query)
        except Exception:
            if i < len(delays):
                time.sleep(delay)
            else:
                break

    if category:
        feed_url = f"{ARXIV_ATOM}/{category}"
        r = httpx.get(feed_url, headers=HEADERS, timeout=60.0)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        return _parse_entries(feed.entries, days=days, query=query)[:limit]

    return []
