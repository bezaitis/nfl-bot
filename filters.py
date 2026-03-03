"""
filters.py — determines whether a transaction is worth posting.

A transaction passes if ANY of the following are true:
  1. Involves the Chicago Bears
  2. Mentions a draft pick (any round/year)
  3. Involves a prominent player (ESPN API → curated star list fallback)

Logic is intentionally modular — each check is a standalone function
so you can tune or swap them independently.
"""

import re
import requests

# ── 1. Chicago Bears filter ────────────────────────────────────────────────────

CHICAGO_TERMS = {"chicago", "bears", "chi"}


def involves_chicago(item: dict) -> bool:
    """True if the transaction involves the Chicago Bears."""
    searchable = (item.get("team", "") + " " + item.get("description", "")).lower()
    return any(term in searchable for term in CHICAGO_TERMS)


# ── 2. Draft pick filter ───────────────────────────────────────────────────────

# Matches things like: "2025 first-round pick", "conditional 3rd-round selection",
# "2026 draft pick", "a first rounder", "picks", etc.
_DRAFT_PICK_PATTERN = re.compile(
    r"""
    (
        \b(20\d{2})\b.*?\b(pick|selection|round|rounder)\b  # "2025 ... pick/round"
        |
        \b(first|second|third|fourth|fifth|sixth|seventh)[-\s]round\b  # "first-round"
        |
        \b\d(st|nd|rd|th)[-\s]round\b                       # "1st-round"
        |
        \bdraft\s+pick\b                                     # "draft pick"
        |
        \bpick\s+swap\b                                      # "pick swap"
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def involves_draft_pick(item: dict) -> bool:
    """True if the transaction description mentions a draft pick."""
    return bool(_DRAFT_PICK_PATTERN.search(item.get("description", "")))


# ── 3. Player prominence filter ───────────────────────────────────────────────

# ESPN athlete endpoint — returns position, status, and fantasy info
ESPN_ATHLETE_SEARCH = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes"
    "?limit=10&active=true&q={name}"
)

# Roster statuses ESPN uses for active starters
_STARTER_STATUSES = {"active", "day-to-day"}

# Curated fallback: ~80 household names across all positions.
# Update this list at the start of each season.
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
    "za'darius smith",
    "aidan hutchinson", "will anderson jr.", "brian burns", "rashan gary",
    "trey hendrickson", "haason reddick", "josh uche", "kayvon thibodeaux",
    "travon walker", "jared verse", "chop robinson", "laiatu latu",
    "nik bonitto", "danielle hunter", "khalil mack",
    # DL (interior)
    "chris jones", "quinnen williams", "dexter lawrence", "jalen carter",
    "jeffery simmons", "jonathan allen", "daron payne",
    "cameron heyward", "nnamdi madubuike", "zach allen", "leonard williams",
    # LB
    "roquan smith",
    "fred warner", "demario davis", "bobby wagner", "tremaine edmunds",
    "zaire franklin", "devin white", "quay walker", "jack campbell",
    "jordyn brooks", "patrick queen", "devin lloyd", "ernest jones iv",
    # CB
    "jalen ramsey", "sauce gardner", "darius slay", "jaire alexander",
    "trevon diggs", "marshon lattimore", "christian gonzalez",
    "devon witherspoon", "joey porter jr.", "nate hobbs",
    "kendall fuller", "patrick surtain ii", "denzel ward",
    "d.j. reed", "tariq woolen", "kelee ringo",
    "derek stingley jr.", "quinyon mitchell", "cooper dejean", "marlon humphrey",
    "byron murphy jr.",
    # S / DB
    "justin simmons",
    "minkah fitzpatrick", "derwin james", "xavier mckinney",
    "budda baker", "harrison smith", "jordan poyer", "kyle hamilton",
    "talanoa hufanga", "quandre diggs", "chamarri conner",
    "kevin byard", "jessie bates iii",
    # K
    "justin tucker",
    "harrison butker", "evan mcpherson", "jake elliott",
    "tyler bass", "brandon aubrey", "cameron dicker",
    "will reichard", "chris boswell",
}


def _extract_player_names(description: str) -> list[str]:
    """
    Heuristically extract candidate player names from a transaction description.
    Looks for 2–3 consecutive capitalized words (First Last, First Middle Last).
    """
    pattern = re.compile(r"\b([A-Z][a-z''\-]+(?:\s[A-Z][a-z''\-]+){1,2})\b")
    return pattern.findall(description)


def _check_espn_prominence(name: str) -> bool:
    """
    Query ESPN's athlete API for a player name.
    Returns True if the player is found and has a starter-level roster status.
    """
    try:
        url = ESPN_ATHLETE_SEARCH.format(name=requests.utils.quote(name))
        resp = requests.get(url, timeout=6)
        resp.raise_for_status()
        data = resp.json()
        athletes = data.get("items", [])
        if not athletes:
            return False

        # Fetch full athlete record for the top result
        athlete_ref = athletes[0]
        ref_url = athlete_ref.get("$ref", "")
        if not ref_url:
            return False

        athlete_resp = requests.get(ref_url, timeout=6)
        athlete_resp.raise_for_status()
        athlete = athlete_resp.json()

        status = athlete.get("status", {}).get("name", "").lower()
        return status in _STARTER_STATUSES

    except Exception as e:
        print(f"[filters] ESPN prominence check failed for '{name}': {e}")
        return None  # None = inconclusive, fall through to curated list


def _check_curated_prominence(name: str) -> bool:
    """Returns True if the name matches any entry in STAR_PLAYERS."""
    return name.lower() in STAR_PLAYERS


def involves_prominent_player(item: dict) -> bool:
    """
    True if any player in the description is prominent.
    Strategy: ESPN API first → curated list as fallback if API is inconclusive.
    """
    candidates = _extract_player_names(item.get("description", ""))
    for name in candidates:
        api_result = _check_espn_prominence(name)
        if api_result is True:
            return True
        if api_result is None:
            # API was inconclusive — fall back to curated list
            if _check_curated_prominence(name):
                return True
        # api_result is False → not a starter per ESPN, skip curated check
    return False


# ── 4. Contract signing filter ────────────────────────────────────────────────

# Tune these to adjust what counts as a "big" signing
AAV_THRESHOLD_M = 30       # $30M+ per year
TOTAL_THRESHOLD_M = 100    # $100M+ total (used only if years can't be parsed)

# Word forms of numbers for "three-year", "four year", etc.
_WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8,
}

# Matches signing language — excludes "traded", "waived", "released"
_SIGNING_PATTERN = re.compile(
    r"\b(signed|re-signed|agreed|extension|contract)\b",
    re.IGNORECASE
)

_TRADE_PATTERN = re.compile(
    r"\b(traded|trade|acquired|exchanged|deal)\b",
    re.IGNORECASE
)

# Matches dollar amounts like "$120 million", "$30M", "120M", "$30.5 million"
_DOLLAR_PATTERN = re.compile(
    r"\$?([\d]+(?:\.[\d]+)?)\s*(million|M)\b",
    re.IGNORECASE
)

# Matches year length like "4-year", "four-year", "4 year", "four year"
_YEARS_PATTERN = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|\d)[-\s]year\b",
    re.IGNORECASE
)


def _is_signing(description: str) -> bool:
    """True if the description looks like a signing rather than a trade."""
    return (
        bool(_SIGNING_PATTERN.search(description))
        and not bool(_TRADE_PATTERN.search(description))
    )


def _parse_contract(description: str) -> tuple[float | None, int | None, float | None]:
    """
    Parse (total_M, years, aav_M) from a contract description.
    Any value that can't be extracted returns None.
    """
    # Extract all dollar amounts — take the largest as total contract value
    dollar_matches = _DOLLAR_PATTERN.findall(description)
    total_M = max((float(v) for v, _ in dollar_matches), default=None)

    # Extract years
    years = None
    years_match = _YEARS_PATTERN.search(description)
    if years_match:
        raw = years_match.group(1).lower()
        years = _WORD_TO_NUM.get(raw, int(raw) if raw.isdigit() else None)

    # Calculate AAV if we have both
    aav_M = None
    if total_M is not None and years:
        aav_M = round(total_M / years, 1)

    return total_M, years, aav_M


def is_big_signing(item: dict) -> tuple[bool, str]:
    """
    Returns (should_post, reason) for signing transactions.

    Logic:
      - Bears signing → always post
      - Has contract value → post if AAV >= threshold (or total if years missing)
      - No contract value → fall back to player prominence
    """
    description = item.get("description", "")

    if not _is_signing(description):
        return False, ""

    # Chicago override — always post Bears signings
    if involves_chicago(item):
        return True, "🐻 Bears signing"

    total_M, years, aav_M = _parse_contract(description)

    if aav_M is not None:
        if aav_M >= AAV_THRESHOLD_M:
            return True, f"💰 ${aav_M}M AAV"
        else:
            return False, ""  # Value found but below threshold — don't fall through

    if total_M is not None and years is None:
        # Have total but couldn't parse years — use total threshold as fallback
        if total_M >= TOTAL_THRESHOLD_M:
            return True, f"💰 ${total_M}M deal"
        else:
            return False, ""

    # No dollar figure at all — fall back to player prominence
    if involves_prominent_player(item):
        return True, "⭐ Notable signing (value undisclosed)"

    return False, ""


# ── Master filter ─────────────────────────────────────────────────────────────

def is_notable_transaction(item: dict) -> tuple[bool, str]:
    """
    Returns (should_post: bool, reason: str).
    Handles both trades and signings — checks all filters.
    The reason string is shown in the Discord embed.
    """
    description = item.get("description", "")

    # Route to signing filter first if it looks like a signing
    if _is_signing(description):
        return is_big_signing(item)

    # Otherwise treat as a trade
    if involves_chicago(item):
        return True, "🐻 Bears trade"
    if involves_draft_pick(item):
        return True, "📋 Draft pick involved"
    if involves_prominent_player(item):
        return True, "⭐ Notable player"

    return False, ""
