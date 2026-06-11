"""
teams.py — NFL team branding (embed color + logo URL) for Discord embeds.

The 32 teams' colors and logos are fetched once from ESPN's teams endpoint
on first use and cached for the life of the process. Lookups fall back to
NFL navy with no logo if the fetch failed or the team is unknown.
"""

import logging
import re

from fetcher import ESPN_TEAMS_URL, TEAM_ALIASES, _normalize_team, _session

logger = logging.getLogger("nfl-bot.teams")

DEFAULT_COLOR = "#013369"  # NFL navy

# nickname (lowercase) → (color hex, logo URL)
_branding: dict[str, tuple[str, str]] = {}
_fetched = False
_nickname_re: re.Pattern | None = None


def _fetch_branding() -> None:
    global _fetched, _nickname_re
    _fetched = True
    try:
        resp = _session.get(ESPN_TEAMS_URL, params={"limit": 32}, timeout=10)
        resp.raise_for_status()
        teams = resp.json().get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
        for entry in teams:
            team = entry.get("team", {})
            nickname = team.get("name", "").lower()  # e.g. "Bears"
            if not nickname:
                continue
            color = team.get("color") or ""
            logos = team.get("logos") or []
            logo_url = logos[0].get("href", "") if logos else ""
            _branding[nickname] = (f"#{color}" if color else DEFAULT_COLOR, logo_url)
        if _branding:
            _nickname_re = re.compile(
                r"\b(" + "|".join(re.escape(n) for n in _branding) + r")\b",
                re.IGNORECASE,
            )
            logger.info("Cached branding for %s teams", len(_branding))
    except Exception as e:
        logger.warning("Team branding fetch failed: %s", e)


def identify_team(text: str) -> str:
    """Return the first team nickname mentioned in text, or '' if none."""
    if not _fetched:
        _fetch_branding()
    if not text:
        return ""
    if _nickname_re:
        match = _nickname_re.search(text)
        if match:
            return match.group(1).lower()
    # Also catch city names / abbreviations via the alias table
    lowered = text.lower()
    for alias, nickname in TEAM_ALIASES.items():
        if len(alias) > 3 and alias in lowered:
            return nickname
    return ""


def get_team_branding(team_name: str) -> tuple[str, str]:
    """
    Return (discord color hex, logo URL) for a team name, nickname,
    abbreviation, or full display name like 'Chicago Bears'.
    Falls back to (NFL navy, no logo) on any miss.
    """
    if not _fetched:
        _fetch_branding()
    nickname = _normalize_team(team_name or "")
    if nickname not in _branding:
        nickname = identify_team(nickname)
    return _branding.get(nickname, (DEFAULT_COLOR, ""))
