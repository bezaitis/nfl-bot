"""
scoring.py — weighted relevance scorer shared by the ESPN and Bluesky pipelines.

Each signal adds (or subtracts) points; items scoring >= POST_THRESHOLD get
posted, highest score first. Signal weights are module constants — tune them
here, then update test_scoring.py to match the new calibration.

Calibration at the current weights:
  - A followed-team mention alone posts.
  - A star player needs a transaction keyword to post.
  - Draft-pick or money language needs a strong transaction keyword.
  - Opinion/hypothetical phrasing kills a post even with strong keywords.
"""

import re

# ── Followed team ─────────────────────────────────────────────────────────────
# The single place the followed team is configured.
FOLLOWED_TEAM = "Bears"
FOLLOWED_TEAM_TERMS = {"chicago", "bears", "chi"}

# Word-boundary match so "chi" hits the abbreviation but not "Chiefs"/"Chip".
_TEAM_RE = re.compile(
    r"\b(?:" + "|".join(sorted(FOLLOWED_TEAM_TERMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# ── Signal weights ────────────────────────────────────────────────────────────
POST_THRESHOLD = 4

TEAM_POINTS = 4            # followed-team mention
STAR_POINTS = 3            # star player name
AAV_HIGH_POINTS = 3        # contract AAV >= AAV_HIGH_M
AAV_MID_POINTS = 2         # contract AAV >= AAV_MID_M
TOTAL_POINTS = 2           # total >= TOTAL_THRESHOLD_M when years unparseable
DRAFT_PICK_POINTS = 2      # draft-pick mention
STRONG_KEYWORD_POINTS = 2  # strong transaction keyword
SOURCE_POINTS = 1          # sourcing / financial corroboration
OPINION_POINTS = -4        # hypothetical/opinion phrasing

AAV_HIGH_M = 20            # $20M+ per year
AAV_MID_M = 10             # $10M+ per year
TOTAL_THRESHOLD_M = 100    # $100M+ total

# ── Star players ──────────────────────────────────────────────────────────────
# Curated list of household names across all positions.
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

# Heuristic player-name extraction: 2–3 consecutive capitalized words.
_NAME_PATTERN = re.compile(r"\b([A-Z][a-z''\-]+(?:\s[A-Z][a-z''\-]+){1,2})\b")

# ── Draft picks ───────────────────────────────────────────────────────────────
# Matches things like: "2025 first-round pick", "conditional 3rd-round selection",
# "2026 draft pick", "a first rounder", "pick swap", etc.
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

# ── Transaction keywords ──────────────────────────────────────────────────────
# Strong signals: almost always indicate a real transaction occurred
_STRONG_KEYWORDS = frozenset({
    "signed", "signing", "signs",
    "traded", "trades",
    "released", "releases",
    "waived", "waives", "waiver",
    "extension",
    "restructured", "restructure", "restructures",
    "injured", "injury",
    "ir", "injured reserve",
    "activated", "activates",
    "void",
    "retired", "retirement", "retires",
    "franchise tag", "tagged",
    "claim", "claimed", "pickup",
    "deal",
})

_STRONG_KW_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in sorted(_STRONG_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Sourcing or financial language that corroborates a transaction
_SOURCE_RE = re.compile(
    r"sources?|per\s|\$\d|million|\baav\b|agreed\s+to|\d-year|one-year|multi-year",
    re.IGNORECASE,
)

# Hypothetical/opinion phrasing — heavily penalized
_OPINION_RE = re.compile(
    r"\b(?:should|could|would|might)\s+(?:have\s+)?(?:traded?|cuts?|signed?|drafted?|released?|waived?|tagged)\b",
    re.IGNORECASE,
)

# ── Contract parsing ──────────────────────────────────────────────────────────
_WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8,
}

# Matches dollar amounts like "$120 million", "$30M", "120M", "$30.5 million"
_DOLLAR_PATTERN = re.compile(
    r"\$?([\d]+(?:\.[\d]+)?)\s*(million|M)\b",
    re.IGNORECASE,
)

# Matches year length like "4-year", "four-year", "4 year", "four year"
_YEARS_PATTERN = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|\d)[-\s]year\b",
    re.IGNORECASE,
)


def _parse_contract(text: str) -> tuple[float | None, int | None, float | None]:
    """
    Parse (total_M, years, aav_M) from a contract description.
    Any value that can't be extracted returns None.
    """
    dollar_matches = _DOLLAR_PATTERN.findall(text)
    total_M = max((float(v) for v, _ in dollar_matches), default=None)

    years = None
    years_match = _YEARS_PATTERN.search(text)
    if years_match:
        raw = years_match.group(1).lower()
        years = _WORD_TO_NUM.get(raw, int(raw) if raw.isdigit() else None)

    aav_M = None
    if total_M is not None and years:
        aav_M = round(total_M / years, 1)

    return total_M, years, aav_M


def _find_star_player(text: str) -> str | None:
    """Return the first STAR_PLAYERS match among extracted candidate names."""
    for name in _NAME_PATTERN.findall(text):
        if name.lower() in STAR_PLAYERS:
            return name
    return None


# ── Scorer ────────────────────────────────────────────────────────────────────
def score_text(text: str, team: str = "") -> tuple[int, list[str]]:
    """
    Score a piece of NFL text (headline + summary, transaction description,
    or Bluesky post). `team` is an optional team field searched alongside
    the text for the followed-team signal.

    Returns (score, reasons). reasons are emoji-tagged strings for the embed.
    """
    score = 0
    reasons: list[str] = []

    if _TEAM_RE.search(f"{team} {text}"):
        score += TEAM_POINTS
        reasons.append(f"🐻 {FOLLOWED_TEAM}")

    star = _find_star_player(text)
    if star:
        score += STAR_POINTS
        reasons.append(f"⭐ {star}")

    total_M, years, aav_M = _parse_contract(text)
    if aav_M is not None and aav_M >= AAV_HIGH_M:
        score += AAV_HIGH_POINTS
        reasons.append(f"💰 ${aav_M}M AAV")
    elif aav_M is not None and aav_M >= AAV_MID_M:
        score += AAV_MID_POINTS
        reasons.append(f"💰 ${aav_M}M AAV")
    elif aav_M is None and total_M is not None and total_M >= TOTAL_THRESHOLD_M:
        score += TOTAL_POINTS
        reasons.append(f"💰 ${total_M}M deal")

    if _DRAFT_PICK_PATTERN.search(text):
        score += DRAFT_PICK_POINTS
        reasons.append("📋 Draft pick")

    if _STRONG_KW_RE.search(text):
        score += STRONG_KEYWORD_POINTS

    if _SOURCE_RE.search(text):
        score += SOURCE_POINTS

    if _OPINION_RE.search(text):
        score += OPINION_POINTS

    return score, reasons
