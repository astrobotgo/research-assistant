import os
import httpx
from dotenv import load_dotenv

load_dotenv()

S2_API_KEY = os.getenv("S2_API_KEY")
S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

def enrich_title(title: str):
    headers = {}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY

    params = {
        "query": title,
        "limit": 1,
        "fields": "paperId,title,year,authors,citationCount,influentialCitationCount,externalIds,url,venue,fieldsOfStudy"
    }

    r = httpx.get(S2_SEARCH_URL, params=params, headers=headers, timeout=30.0)
    r.raise_for_status()

    data = r.json().get("data", [])
    return data[0] if data else None
