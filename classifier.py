"""
classifier.py — LLM-based NFL news relevance classifier (Gemini).

Result cache: entity-based, 60-min TTL — same story from multiple posts hits LLM once.
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


# ── Static notable player list (fallback when Gemini is uncertain) ────────────

STAR_PLAYERS: set[str] = {
    # QBs
    "patrick mahomes", "josh allen", "lamar jackson", "joe burrow", "jalen hurts",
    "dak prescott", "tua tagovailoa", "justin herbert", "jordan love", "brock purdy",
    "caleb williams", "jayden daniels", "sam darnold",
    "c.j. stroud", "anthony richardson", "drake maye", "bo nix",
    "matthew stafford", "baker mayfield", "kyler murray", "trevor lawrence",
    "geno smith", "kirk cousins", "russell wilson", "aaron rodgers", "jared goff",
    # RBs
    "christian mccaffrey", "derrick henry", "saquon barkley", "de'von achane",
    "josh jacobs", "jahmyr gibbs", "breece hall", "bijan robinson", "james cook",
    "kyren williams", "jonathan taylor", "alvin kamara", "tony pollard",
    "travis etienne", "joe mixon", "d'andre swift", "najee harris",
    "david montgomery", "isaiah pacheco", "rachaad white", "aaron jones",
    "rhamondre stevenson", "zamir white", "chuba hubbard", "javonte williams",
    "ray davis",
    # WRs
    "tyreek hill", "davante adams", "stefon diggs", "a.j. brown", "justin jefferson",
    "ceedee lamb", "deebo samuel", "amon-ra st. brown", "puka nacua", "jaylen waddle",
    "chris olave", "drake london", "courtland sutton", "michael pittman",
    "tee higgins", "george pickens", "jordan addison", "keenan allen",
    "mike evans", "dk metcalf", "nico collins", "garrett wilson",
    "tank dell", "rashee rice", "zay flowers", "marvin harrison jr.",
    "rome odunze", "xavier worthy", "ladd mcconkey", "dj moore",
    "tyler lockett", "diontae johnson", "jaxon smith-njigba",
    "xavier legette", "brian thomas jr.", "wan'dale robinson",
    "jameson williams", "rashid shaheed", "christian watson",
    "ja'marr chase", "malik nabers", "terry mclaurin", "jerry jeudy",
    # TEs
    "travis kelce", "sam laporta", "mark andrews", "t.j. hockenson", "evan engram",
    "dalton kincaid", "kyle pitts", "pat freiermuth", "david njoku",
    "george kittle", "trey mcbride", "jake ferguson", "brock bowers",
    "isaiah likely", "cade otton", "tucker kraft", "jonnu smith",
    # OL
    "trent williams", "lane johnson", "tristan wirfs", "penei sewell",
    "rashawn slater", "christian darrisaw", "darnell wright",
    "paris johnson jr.", "zion johnson", "joe thuney",
    "garrett bolles", "creed humphrey", "quinn meinerz", "quenton nelson",
    "chris lindstrom", "trey smith", "tyler linderbaum", "laremy tunsil",
    # Edge / Pass Rush
    "micah parsons", "myles garrett", "maxx crosby", "nick bosa", "tj watt",
    "za'darius smith", "aidan hutchinson", "will anderson jr.", "brian burns",
    "rashan gary", "trey hendrickson", "haason reddick", "josh uche",
    "kayvon thibodeaux", "travon walker", "jared verse", "chop robinson",
    "laiatu latu", "nik bonitto", "danielle hunter", "khalil mack",
    # DL (interior)
    "chris jones", "quinnen williams", "dexter lawrence", "jalen carter",
    "jeffery simmons", "jonathan allen", "daron payne",
    "cameron heyward", "nnamdi madubuike", "zach allen", "leonard williams",
    # LB
    "roquan smith", "fred warner", "demario davis", "bobby wagner",
    "tremaine edmunds", "zaire franklin", "devin white", "quay walker",
    "jack campbell", "jordyn brooks", "patrick queen", "devin lloyd", "ernest jones iv",
    # CB
    "jalen ramsey", "sauce gardner", "darius slay", "jaire alexander",
    "trevon diggs", "marshon lattimore", "christian gonzalez",
    "devon witherspoon", "joey porter jr.", "nate hobbs",
    "kendall fuller", "patrick surtain ii", "denzel ward",
    "d.j. reed", "tariq woolen", "kelee ringo",
    "derek stingley jr.", "quinyon mitchell", "cooper dejean", "marlon humphrey",
    "byron murphy jr.",
    # S / DB
    "justin simmons", "minkah fitzpatrick", "derwin james", "xavier mckinney",
    "budda baker", "harrison smith", "jordan poyer", "kyle hamilton",
    "talanoa hufanga", "quandre diggs", "chamarri conner",
    "kevin byard", "jessie bates iii",
    # K
    "justin tucker", "harrison butker", "evan mcpherson", "jake elliott",
    "tyler bass", "brandon aubrey", "cameron dicker",
    "will reichard", "chris boswell",
}

def _mentions_star_player(text: str) -> bool:
    text_lower = text.lower()
    return any(p in text_lower for p in STAR_PLAYERS)


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
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
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            max_tokens=60,
            temperature=0,
        )
        raw = response["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        result = json.loads(raw)
        should_post = bool(result.get("should_post", False))
    except Exception as e:
        print(f"[classifier] LLM call failed: {e}")
        should_post = False
    # Fallback: if Gemini said no, still post if a known star player is mentioned
    if not should_post:
        should_post = _mentions_star_player(text)
    if fp:
        _store_cache(fp, should_post)
    return should_post
