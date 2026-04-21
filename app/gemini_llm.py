import os

import httpx


def gemini_generate(prompt: str, timeout: float = 180.0) -> str:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    model = (os.getenv("GEMINI_MODEL") or "gemini-1.5-flash").strip()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    r = httpx.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    text_parts: list[str] = []
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            t = part.get("text")
            if t:
                text_parts.append(t)
    out = "\n".join(text_parts).strip()
    if not out:
        raise RuntimeError("Gemini returned empty text")
    return out

