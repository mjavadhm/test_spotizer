"""LLM-based music recommendations (Gemini REST API, no extra dependencies).

Port of the old bot's llm_service.py: same prompt, same model, but called
directly over HTTPS instead of through langchain.
"""
import json
import logging

import aiohttp

from ..config import get_settings

logger = logging.getLogger(__name__)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)

PROMPT_TEMPLATE = """You are a knowledgeable music recommendation assistant.
Based on the user's listening history below, recommend 5 new songs that they are likely to enjoy.
Focus on similar genres, moods, and artists, but do not recommend songs from the input list.

Liked Songs (High rating or frequent listens):
{liked}

Disliked Songs (Explicitly disliked):
{disliked}

Return ONLY a JSON array of 5 objects, each with "artist" and "title" string keys. No other text."""


async def generate_recommendations(liked: list[str], disliked: list[str]) -> list[dict]:
    """Returns a list of {"artist": ..., "title": ...} dicts (may be empty)."""
    api_key = get_settings().GEMINI_API_KEY
    if not api_key:
        logger.warning("GEMINI_API_KEY is not set - recommendations disabled")
        return []

    prompt = PROMPT_TEMPLATE.format(
        liked="\n".join(f"- {s}" for s in liked) or "- (no data)",
        disliked="\n".join(f"- {s}" for s in disliked) or "None",
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json",
        },
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        ) as session:
            async with session.post(
                GEMINI_URL, params={"key": api_key}, json=payload
            ) as resp:
                data = await resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        recs = json.loads(text)
        if isinstance(recs, dict):
            recs = [recs]
        return [
            r for r in recs
            if isinstance(r, dict) and r.get("title")
        ][:10]
    except Exception as e:  # noqa: BLE001 - recommendations must never crash the API
        logger.error("Gemini recommendation failed: %s", e)
        return []
