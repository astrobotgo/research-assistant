import os
import httpx
from dotenv import load_dotenv

load_dotenv()

S2_API_KEY = os.getenv("S2_API_KEY")
S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_RECOMMEND_URL = "https://api.semanticscholar.org/recommendations/v1/papers/forpaper"


def _s2_headers() -> dict:
    return {"x-api-key": S2_API_KEY} if S2_API_KEY else {}


def enrich_title(title: str) -> dict | None:
    params = {
        "query": title,
        "limit": 1,
        "fields": "paperId,title,year,authors,citationCount,influentialCitationCount,externalIds,url,venue,fieldsOfStudy",
    }
    r = httpx.get(S2_SEARCH_URL, params=params, headers=_s2_headers(), timeout=30.0)
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None


def get_related_papers(s2_paper_id: str, n: int = 3) -> list[dict]:
    """
    Use Semantic Scholar's recommendations API to find papers related to the
    given S2 paper ID. Returns up to n results with title, authors, year, url.
    """
    if not s2_paper_id:
        return []
    params = {
        "fields": "title,authors,year,externalIds,url",
        "limit": max(n, 5),  # fetch a few extra so we can filter duds
    }
    try:
        r = httpx.get(
            f"{S2_RECOMMEND_URL}/{s2_paper_id}",
            params=params,
            headers=_s2_headers(),
            timeout=20.0,
        )
        if r.status_code == 404:
            return []
        r.raise_for_status()
        papers = r.json().get("recommendedPapers") or []
        out = []
        for p in papers:
            title = (p.get("title") or "").strip()
            if not title:
                continue
            authors = [a.get("name", "") for a in (p.get("authors") or [])]
            author_str = authors[0] + " et al." if len(authors) > 1 else (authors[0] if authors else "")
            year = p.get("year")
            # Build URL: prefer S2 url, fall back to arXiv if available
            url = p.get("url") or ""
            ext = p.get("externalIds") or {}
            if ext.get("ArXiv"):
                url = f"https://arxiv.org/abs/{ext['ArXiv']}"
            out.append({"title": title, "authors": author_str, "year": year, "url": url})
            if len(out) >= n:
                break
        return out
    except Exception:
        return []
