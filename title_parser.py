"""
title_parser.py — Converts raw ESPN transaction descriptions into structured one-liners.

Format: "Player Name (POS) action — Team [— Nyrs, $XM]"

Examples:
  "Joe Burrow (QB) re-signs with Cincinnati — 5yr, $275M"
  "Tyreek Hill (WR) traded to Miami"
  "Jamie Collins (LB) released by New England"
"""

import re

_POSITION_MAP = {
    "quarterback": "QB",
    "running back": "RB",
    "wide receiver": "WR",
    "tight end": "TE",
    "linebacker": "LB",
    "cornerback": "CB",
    "safety": "S",
    "defensive tackle": "DT",
    "defensive end": "DE",
    "offensive tackle": "OT",
    "offensive guard": "OG",
    "guard": "G",
    "center": "C",
    "kicker": "K",
    "punter": "P",
    "fullback": "FB",
    "long snapper": "LS",
    "edge rusher": "EDGE",
    "defensive back": "DB",
}

_WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8,
}

# Ordered action patterns — first match wins.
# Tuple: (compiled regex, display label)
_ACTION_PATTERNS = [
    (re.compile(r"\bplaced\b.{0,50}?\binjured reserve\b", re.I | re.S), "placed on IR by"),
    (re.compile(r"\bplaced\b.{0,50}?\bpractice squad\b", re.I | re.S), "to practice squad by"),
    (re.compile(r"\bsigned\b.{0,50}?\bpractice squad\b", re.I | re.S), "signs to practice squad"),
    (re.compile(r"\bre-sign(ed|s)?\b", re.I), "re-signs with"),
    (re.compile(r"\bextension\b", re.I), "extends with"),
    (re.compile(r"\btrade[d]?\b", re.I), "traded to"),
    (re.compile(r"\b(signed|agreed to terms)\b", re.I), "signs with"),
    (re.compile(r"\breleased?\b", re.I), "released by"),
    (re.compile(r"\bwaived?\b", re.I), "waived by"),
    (re.compile(r"\bactivated?\b", re.I), "activated by"),
    (re.compile(r"\bretired?\b", re.I), "retires from"),
]

_DOLLAR_RE = re.compile(r"\$?([\d]+(?:\.[\d]+)?)\s*(million|M)\b", re.I)
_YEARS_RE = re.compile(r"\b(one|two|three|four|five|six|seven|eight|\d)[-\s]year\b", re.I)
_NAME_RE = re.compile(r"\b([A-Z][a-z'\-]+(?:\s[A-Z][a-z'\-]+){1,2})\b")
_TRADE_DEST_RE = re.compile(r"\bto the ([A-Z][a-z]+(?: [A-Z][a-z]+){0,2})\b")

# Words that look like names but aren't player names
_COMMON_WORDS = frozenset({
    "The", "NFL", "AFC", "NFC", "Pro", "Super", "Bowl", "National", "Football",
    "League", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
    # Team nicknames
    "Bears", "Packers", "Vikings", "Lions", "Cowboys", "Eagles", "Giants",
    "Commanders", "Seahawks", "Rams", "Cardinals", "Chiefs", "Chargers",
    "Broncos", "Raiders", "Bills", "Dolphins", "Patriots", "Jets", "Ravens",
    "Steelers", "Browns", "Bengals", "Texans", "Colts", "Jaguars", "Titans",
    "Saints", "Buccaneers", "Falcons", "Panthers",
})


def _extract_position(text: str) -> str:
    lower = text.lower()
    for phrase, abbr in _POSITION_MAP.items():
        if phrase in lower:
            return abbr
    return ""


def _extract_action(text: str) -> str:
    for pattern, label in _ACTION_PATTERNS:
        if pattern.search(text):
            return label
    return "move involving"


def _extract_player(text: str, pos_abbr: str) -> str:
    """
    Prefer the capitalized name immediately following the position word.
    Falls back to the first valid multi-word capitalized sequence.
    """
    if pos_abbr:
        for phrase, abbr in _POSITION_MAP.items():
            if abbr != pos_abbr:
                continue
            m = re.search(
                re.escape(phrase) + r"\s+([A-Z][a-z'\-]+(?:\s[A-Z][a-z'\-]+){1,2})",
                text, re.I,
            )
            if m:
                return m.group(1)

    for candidate in _NAME_RE.findall(text):
        parts = candidate.split()
        if len(parts) >= 2 and not any(p in _COMMON_WORDS for p in parts):
            return candidate
    return ""


def _extract_contract(text: str) -> str:
    dollar_matches = _DOLLAR_RE.findall(text)
    total_M = max((float(v) for v, _ in dollar_matches), default=None)

    years = None
    m = _YEARS_RE.search(text)
    if m:
        raw = m.group(1).lower()
        years = _WORD_TO_NUM.get(raw, int(raw) if raw.isdigit() else None)

    parts = []
    if years:
        parts.append(f"{years}yr")
    if total_M is not None:
        val = int(total_M) if total_M == int(total_M) else total_M
        parts.append(f"${val}M")
    return ", ".join(parts)


def build_structured_title(item: dict) -> str:
    """
    Build a structured one-line title from a raw ESPN transaction dict.
    Falls back to truncated raw description if extraction is insufficient.

    Format: "Player Name (POS) action — Team [— contract]"
    """
    description = item.get("description", "")
    team = item.get("team", "")

    pos = _extract_position(description)
    player = _extract_player(description, pos)
    action = _extract_action(description)
    contract = _extract_contract(description)

    if not player:
        return description[:100] + ("…" if len(description) > 100 else "")

    player_str = f"{player} ({pos})" if pos else player

    if action == "traded to":
        dest_match = _TRADE_DEST_RE.search(description)
        team_str = dest_match.group(1) if dest_match else team
    else:
        team_str = team

    action_str = f"{action} {team_str}".strip() if team_str else action

    title = f"{player_str} {action_str}"
    if contract:
        title += f" — {contract}"

    return title
