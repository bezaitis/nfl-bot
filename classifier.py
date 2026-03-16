"""
classifier.py — Shared LLM-based NFL news relevance classifier.

Player list: read from notable_players_cache.json (written by bot.py on schedule).
Result cache: entity-based, 60-min TTL — same story from Bluesky and ESPN hits LLM once.
"""
import json
import os
import re
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

def _openrouter(model: str, messages: list[dict], **kwargs) -> dict:
    api_key = os.getenv("OPENROUTER_API_KEY")
    resp = requests.post(
        _OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, **kwargs},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Notable player list ───────────────────────────────────────────────────────

_PLAYERS_CACHE_PATH = Path(__file__).parent / "notable_players_cache.json"

_PLAYER_REFRESH_PROMPT = """\
List the most notable current NFL players — include:
- All starting QBs
- Pro Bowl caliber starters at every position
- Any player with significant NFL career history still active (e.g. Aaron Rodgers even on a low salary)
- Players whose trade, signing, cut, or retirement would be major NFL news

Return ONLY a JSON array of full player names in lowercase. No explanation.
Example: ["patrick mahomes", "lamar jackson", "aaron rodgers"]
"""

def refresh_notable_players() -> None:
    """Fetch current notable NFL players via Perplexity Sonar and write cache to disk.
    Called by bot.py — never called inline during classification."""
    global _SYSTEM_PROMPT
    try:
        resp = _openrouter(
            model="perplexity/sonar",
            messages=[{"role": "user", "content": _PLAYER_REFRESH_PROMPT}],
            temperature=0,
        )
        players = json.loads(resp["choices"][0]["message"]["content"])
        if isinstance(players, dict):
            players = next(iter(players.values()))
        _PLAYERS_CACHE_PATH.write_text(json.dumps({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "players": [p.lower() for p in players],
        }))
        _SYSTEM_PROMPT = None  # bust cached prompt so next classify() rebuilds it
        print(f"[classifier] Player list refreshed ({len(players)} players)")
    except Exception as e:
        print(f"[classifier] Player list refresh failed: {e}")

def _get_notable_players() -> list[str]:
    """Read from cache file. Falls back to static STAR_PLAYERS if cache missing."""
    if _PLAYERS_CACHE_PATH.exists():
        try:
            return json.loads(_PLAYERS_CACHE_PATH.read_text())["players"]
        except Exception:
            pass
    from filters import STAR_PLAYERS
    return list(STAR_PLAYERS)


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT: str | None = None

_CLASSIFIER_SYSTEM_BASE = """\
You are a senior NFL news editor. Decide if a post is important enough to push to an NFL news Discord channel.

Surface news that matters to NFL fans — not just by dollar value, but by football significance. Ask: would a knowledgeable NFL fan care about this?

ALWAYS POST confirmed transactions for significant players, regardless of contract size:
- Hall-of-fame caliber or storied players: a retirement, release, trade, or signing matters even on a veteran-minimum deal
- Any franchise QB move (even a backup QB trade can shift a team's season)
- Any trade involving draft picks
- Contract extensions and restructures for established starters
- Suspensions (league-wide impact)
- Franchise tag decisions

POST based on financial significance for non-household-name players:
- Signings with implied AAV >= $10M/year
- Multi-year deals worth $50M+ total

POST Bears-specific confirmed news only:
- Bears player signings, cuts, trades, injuries, retirements, coaching changes
- NOT mock draft projections or "Bears could target..." speculation

DO NOT POST:
- Opinion, analysis, hot takes, or commentary
- Mock drafts or draft projections (even Bears-specific)
- Hypotheticals ("if they sign him...", "what if they trade for...")
- Game recaps, stats, or performance analysis
- Unconfirmed rumors with no sourcing
- Depth chart shuffles or practice squad moves for non-notable players

Respond with JSON only: { "should_post": true/false, "reason": "one sentence" }
"""

def _get_system_prompt() -> str:
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        players_str = ", ".join(sorted(_get_notable_players()))
        _SYSTEM_PROMPT = (
            _CLASSIFIER_SYSTEM_BASE
            + f"\n\nCurrent notable NFL players (always significant regardless of contract size):\n{players_str}"
        )
    return _SYSTEM_PROMPT


# ── Result cache (entity-based, cross-source) ─────────────────────────────────

_NAME_RE = re.compile(r"\b([A-Z][a-z''\-]+(?:\s[A-Z][a-z''\-]+)+)\b")
_RESULT_CACHE: list[tuple[frozenset[str], bool, datetime]] = []
_CACHE_TTL = timedelta(minutes=60)

def _entities(text: str) -> frozenset[str]:
    return frozenset(n.lower() for n in _NAME_RE.findall(text))

def _check_cache(fp: frozenset[str]) -> bool | None:
    now = datetime.now(timezone.utc)
    for cached_fp, result, ts in _RESULT_CACHE:
        if now - ts < _CACHE_TTL and fp & cached_fp:
            return result
    return None

def _store_cache(fp: frozenset[str], result: bool) -> None:
    now = datetime.now(timezone.utc)
    _RESULT_CACHE[:] = [(f, r, t) for f, r, t in _RESULT_CACHE if now - t < _CACHE_TTL]
    _RESULT_CACHE.append((fp, result, now))


# ── Public API ────────────────────────────────────────────────────────────────

def classify(text: str) -> bool:
    """Returns True if this is important NFL news worth posting to Discord."""
    fp = _entities(text)
    if fp:
        cached = _check_cache(fp)
        if cached is not None:
            return cached
    try:
        response = _openrouter(
            model="google/gemini-2.5-flash",
            messages=[
                {"role": "system", "content": _get_system_prompt()},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            max_tokens=60,
            temperature=0,
        )
        result = json.loads(response["choices"][0]["message"]["content"])
        should_post = bool(result.get("should_post", False))
    except Exception as e:
        print(f"[classifier] LLM call failed: {e}")
        should_post = False
    if fp:
        _store_cache(fp, should_post)
    return should_post
