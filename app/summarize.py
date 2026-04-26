import json
import os
import re

import httpx
from dotenv import load_dotenv

from app.gemini_llm import gemini_generate

load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:32b")

def _extract_json(text: str):
    text = text.strip()

    # Remove ```json ... ``` fences if present
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    # If the whole thing is JSON, parse it
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try to find the first JSON object inside the text
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    return None

def _paper_summary_prompt(title: str, abstract: str) -> str:
    return f"""
You are a research analyst.

Return ONLY valid JSON.
Do not use markdown fences.
Do not add commentary.

Required JSON keys:
one_sentence_summary
methods
limitations
novelty_claim
relevance_score_1_to_10

Title: {title}

Abstract:
{abstract}
"""


def summarize_paper(title: str, abstract: str):
    prompt = _paper_summary_prompt(title, abstract)
    try:
        text = gemini_generate(prompt=prompt, timeout=180.0)
        parsed = _extract_json(text)
        if parsed:
            return parsed
    except Exception:
        pass

    r = httpx.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
        },
        timeout=180.0,
    )
    r.raise_for_status()
    text = r.json()["response"].strip()

    parsed = _extract_json(text)
    if parsed:
        return parsed

    return {
        "one_sentence_summary": text,
        "methods": "",
        "limitations": "",
        "novelty_claim": "",
        "relevance_score_1_to_10": ""
    }


def summarize_with_ollama(title: str, abstract: str):
    # Backward-compatible alias; now Gemini-first with Ollama fallback.
    return summarize_paper(title, abstract)


def enrich_background(title: str, abstract: str) -> dict:
    """
    Ask the LLM to draw on its training knowledge to provide scientific background
    for a paper, plus a list of key prior papers with arXiv IDs where known.

    Returns a dict:
        text       : str   — background paragraph (3-5 sentences)
        key_papers : list  — [{"title": ..., "year": ..., "arxiv_id": ..., "reason": ...}]
        verified   : list  — key_papers entries confirmed to exist on arXiv (with arxiv_url added)
    """
    from app.fetch_arxiv import lookup_arxiv_paper

    prompt = f"""You are an expert astrophysicist with deep knowledge of the literature.

Given the title and abstract of a new paper, return JSON with two fields:

1. "background": 3-5 sentences of scientific context from the prior literature. Cover:
   - Key prior results or constraints this work builds on or challenges
   - Relevant surveys, instruments, simulations, or datasets
   - Known tensions, debates, or open questions in this subfield
   - Where this paper fits in the field's progression
   Write for an expert reader. Be specific — cite results, parameter values, survey names.

2. "key_papers": list of up to 5 important prior papers most relevant to understanding this work.
   For each paper include:
   - "title": exact paper title
   - "year": publication year (integer)
   - "arxiv_id": arXiv ID if you are confident it is correct (e.g. "2301.04527"), else null
   - "reason": one sentence on why this paper is relevant

Return ONLY valid JSON. No markdown fences.

Title: {title}

Abstract: {abstract[:1500]}"""

    raw = ""
    try:
        raw = gemini_generate(prompt=prompt, timeout=120.0).strip()
    except Exception:
        pass

    if not raw:
        try:
            r = httpx.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json"},
                timeout=180.0,
            )
            r.raise_for_status()
            raw = r.json().get("response", "").strip()
        except Exception:
            return {"text": "", "key_papers": [], "verified": []}

    # Parse JSON, tolerating markdown fences
    parsed = None
    try:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.DOTALL).strip()
        parsed = json.loads(cleaned)
    except Exception:
        pass

    if not isinstance(parsed, dict):
        return {"text": raw, "key_papers": [], "verified": []}

    background_text = parsed.get("background", "").strip()
    key_papers = parsed.get("key_papers", [])
    if not isinstance(key_papers, list):
        key_papers = []

    # Verify each cited paper against arXiv
    verified = []
    for paper in key_papers[:5]:
        if not isinstance(paper, dict):
            continue
        ptitle = paper.get("title", "")
        pid = paper.get("arxiv_id") or ""
        result = lookup_arxiv_paper(title=ptitle, arxiv_id=pid)
        if result:
            verified.append({**paper, "arxiv_url": result["arxiv_url"]})

    return {"text": background_text, "key_papers": key_papers, "verified": verified}
