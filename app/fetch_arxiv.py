from datetime import datetime, timezone, timedelta
import time

import feedparser
import httpx

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_ATOM = "https://rss.arxiv.org/atom"

HEADERS = {
    "User-Agent": "research-assistant/0.1"
}

# Delays (seconds) between successive retry attempts.
_RETRY_DELAYS = [5, 15, 45]


def _should_retry(status: int) -> bool:
    return status in {429, 500, 502, 503, 504}


def _get_with_retry(url: str, params: dict | None = None, timeout: float = 90.0) -> httpx.Response:
    """GET with exponential-backoff retry for rate-limit and transient server errors."""
    last_exc: Exception | None = None
    for attempt, delay in enumerate(_RETRY_DELAYS + [0], start=1):
        try:
            r = httpx.get(url, params=params, headers=HEADERS, timeout=timeout)
            if _should_retry(r.status_code):
                if attempt <= len(_RETRY_DELAYS):
                    time.sleep(delay)
                    continue
                r.raise_for_status()
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError as e:
            last_exc = e
            if attempt <= len(_RETRY_DELAYS):
                time.sleep(delay)
        except Exception as e:
            last_exc = e
            if attempt <= len(_RETRY_DELAYS):
                time.sleep(delay)
    raise last_exc or RuntimeError(f"All retries exhausted for {url}")


def _matches_topic(text: str, include_any: tuple[str, ...], exclude_any: tuple[str, ...]) -> bool:
    if not text:
        return False

    lowered = text.lower()
    if any(term in lowered for term in exclude_any):
        return False
    if not include_any:
        return True
    return any(term in lowered for term in include_any)


def _parse_entries(
    entries,
    days: int,
    include_any: tuple[str, ...] = (),
    exclude_any: tuple[str, ...] = (),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    papers = []

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
        text = f"{title}\n{summary}"

        if not _matches_topic(text, include_any=include_any, exclude_any=exclude_any):
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


def lookup_arxiv_paper(title: str = "", arxiv_id: str = "") -> dict | None:
    """
    Try to find an arXiv paper by ID or title search.
    Returns a dict with 'id', 'title', 'arxiv_url', 'pdf_url' if found, else None.
    The ID form is tried first (exact); title search is used as fallback.
    """
    def _clean_id(raw: str) -> str:
        raw = raw.strip()
        for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv:", "arXiv:"):
            if raw.lower().startswith(prefix.lower()):
                raw = raw[len(prefix):]
        return raw.split("v")[0]  # strip version suffix

    def _title_similarity(a: str, b: str) -> float:
        a_words = set(a.lower().split())
        b_words = set(b.lower().split())
        if not a_words or not b_words:
            return 0.0
        return len(a_words & b_words) / max(len(a_words), len(b_words))

    def _parse_first(feed_text: str) -> dict | None:
        feed = feedparser.parse(feed_text)
        if not feed.entries:
            return None
        entry = feed.entries[0]
        eid = getattr(entry, "id", "")
        etitle = " ".join(getattr(entry, "title", "").split())
        pdf_url = None
        for link in getattr(entry, "links", []):
            href = getattr(link, "href", "")
            ltype = getattr(link, "type", "")
            if ltype == "application/pdf" or href.endswith(".pdf"):
                pdf_url = href
                break
        abs_url = eid if "arxiv.org" in eid else f"https://arxiv.org/abs/{_clean_id(eid)}"
        return {"id": eid, "title": etitle, "arxiv_url": abs_url, "pdf_url": pdf_url}

    # 1. Direct ID lookup
    if arxiv_id:
        clean = _clean_id(arxiv_id)
        try:
            r = _get_with_retry(ARXIV_API, params={"id_list": clean})
            result = _parse_first(r.text)
            if result:
                return result
        except Exception:
            pass

    # 2. Title search
    if title:
        query_title = title[:120]
        try:
            r = _get_with_retry(ARXIV_API, params={
                "search_query": f'ti:"{query_title}"',
                "max_results": 3,
                "sortBy": "relevance",
            })
            feed = feedparser.parse(r.text)
            for entry in feed.entries:
                etitle = " ".join(getattr(entry, "title", "").split())
                if _title_similarity(title, etitle) >= 0.7:
                    eid = getattr(entry, "id", "")
                    pdf_url = None
                    for link in getattr(entry, "links", []):
                        href = getattr(link, "href", "")
                        ltype = getattr(link, "type", "")
                        if ltype == "application/pdf" or href.endswith(".pdf"):
                            pdf_url = href
                            break
                    abs_url = eid if "arxiv.org" in eid else f"https://arxiv.org/abs/{_clean_id(eid)}"
                    return {"id": eid, "title": etitle, "arxiv_url": abs_url, "pdf_url": pdf_url}
        except Exception:
            pass

    return None


def fetch_recent_arxiv(
    query: str,
    category: str = "",
    days: int = 7,
    limit: int = 10,
    include_any: tuple[str, ...] = (),
    exclude_any: tuple[str, ...] = (),
):
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

    try:
        r = _get_with_retry(ARXIV_API, params=params)
        feed = feedparser.parse(r.text)
        return _parse_entries(
            feed.entries,
            days=days,
            include_any=include_any,
            exclude_any=exclude_any,
        )
    except Exception:
        pass

    # Atom feed fallback (category-scoped only)
    if category:
        try:
            r = _get_with_retry(f"{ARXIV_ATOM}/{category}", timeout=60.0)
            feed = feedparser.parse(r.text)
            return _parse_entries(
                feed.entries,
                days=days,
                include_any=include_any,
                exclude_any=exclude_any,
            )[:limit]
        except Exception:
            pass

    return []
