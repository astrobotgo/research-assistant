import os
import json
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
