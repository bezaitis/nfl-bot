"""
fetcher.py — pulls NFL transactions and news from ESPN's public APIs and RSS feed.
No API key required.
"""

import hashlib
import urllib.parse

import feedparser
import requests

ESPN_TRANSACTIONS_URL = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/transactions"
    "?limit=50"
)
ESPN_NEWS_RSS = "https://www.espn.com/espn/rss/nfl/news"
ESPN_ATHLETES_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes"

# Map of common team name variants → normalized lowercase
TEAM_ALIASES = {
    "chi": "bears", "chicago": "bears",
    "gb": "packers", "green bay": "packers",
    "min": "vikings", "minnesota": "vikings",
    "det": "lions", "detroit": "lions",
    "dal": "cowboys", "dallas": "cowboys",
    "phi": "eagles", "philadelphia": "eagles",
    "nyg": "giants", "new york giants": "giants",
    "was": "commanders", "washington": "commanders",
    "sf": "49ers", "san francisco": "49ers",
    "sea": "seahawks", "seattle": "seahawks",
    "lar": "rams", "los angeles rams": "rams",
    "arz": "cardinals", "arizona": "cardinals",
    "kc": "chiefs", "kansas city": "chiefs",
    "lac": "chargers", "los angeles chargers": "chargers",
    "den": "broncos", "denver": "broncos",
    "lv": "raiders", "las vegas": "raiders",
    "buf": "bills", "buffalo": "bills",
    "mia": "dolphins", "miami": "dolphins",
    "ne": "patriots", "new england": "patriots",
    "nyj": "jets", "new york jets": "jets",
    "bal": "ravens", "baltimore": "ravens",
    "pit": "steelers", "pittsburgh": "steelers",
    "cle": "browns", "cleveland": "browns",
    "cin": "bengals", "cincinnati": "bengals",
    "hou": "texans", "houston": "texans",
    "ind": "colts", "indianapolis": "colts",
    "jax": "jaguars", "jacksonville": "jaguars",
    "ten": "titans", "tennessee": "titans",
    "no": "saints", "new orleans": "saints",
    "tb": "buccaneers", "tampa bay": "buccaneers",
    "atl": "falcons", "atlanta": "falcons",
    "car": "panthers", "carolina": "panthers",
}


def _normalize_team(query: str) -> str:
    """Normalize a team query to a lowercase team nickname."""
    q = query.lower().strip()
    return TEAM_ALIASES.get(q, q)


def get_all_transactions(limit: int = 50) -> list[dict]:
    """
    Fetch raw NFL transactions — no filtering applied.
    Used by the auto-post loop, which passes items through filters.py.
    """
    try:
        resp = requests.get(ESPN_TRANSACTIONS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[fetcher] Transaction fetch failed: {e}")
        return []

    items = []
    for t in data.get("items", []):
        description = t.get("description", "").strip()
        if not description:
            continue
        team_name = ""
        team_ref = t.get("team")
        if isinstance(team_ref, dict):
            team_name = team_ref.get("displayName", "")
        uid = hashlib.md5(description.encode()).hexdigest()
        items.append({
            "id": uid,
            "title": description[:80] + ("…" if len(description) > 80 else ""),
            "description": description,
            "team": team_name,
            "date": t.get("date", ""),
        })
        if len(items) >= limit:
            break
    return items


def get_transactions(limit: int = 8, team_filter: str | None = None) -> list[dict]:
    """
    Fetch NFL transactions from ESPN's core API.
    Optionally filter by team name/abbreviation.
    Returns a list of dicts: {id, title, description, team}
    """
    try:
        resp = requests.get(ESPN_TRANSACTIONS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[fetcher] Transaction fetch failed: {e}")
        return []

    normalized_filter = _normalize_team(team_filter) if team_filter else None

    items = []
    for t in data.get("items", []):
        description = t.get("description", "").strip()
        if not description:
            continue

        team_name = ""
        team_ref = t.get("team")
        if isinstance(team_ref, dict):
            team_name = team_ref.get("displayName", "")

        # Filter by team if requested
        if normalized_filter:
            searchable = (team_name + " " + description).lower()
            if normalized_filter not in searchable:
                continue

        uid = hashlib.md5(description.encode()).hexdigest()
        items.append(
            {
                "id": uid,
                "title": description[:80] + ("…" if len(description) > 80 else ""),
                "description": description,
                "team": team_name,
                "date": t.get("date", ""),
            }
        )

        if len(items) >= limit:
            break

    return items


def get_news(limit: int = 5, team_filter: str | None = None) -> list[dict]:
    """
    Fetch latest NFL headlines from ESPN's RSS feed.
    Optionally filter by team name/abbreviation (client-side).
    Returns a list of dicts: {title, link, summary}
    """
    try:
        feed = feedparser.parse(ESPN_NEWS_RSS)
        normalized = _normalize_team(team_filter) if team_filter else None
        items = []
        for entry in feed.entries:
            title = entry.get("title", "No title")
            summary = entry.get("summary", "")
            if normalized:
                searchable = (title + " " + summary).lower()
                if normalized not in searchable:
                    continue
            items.append(
                {
                    "title": title,
                    "link": entry.get("link", ""),
                    "summary": summary,
                }
            )
            if len(items) >= limit:
                break
        return items
    except Exception as e:
        print(f"[fetcher] News RSS fetch failed: {e}")
        return []


def get_player(name: str) -> dict | None:
    """
    Look up an NFL player by name via ESPN's public athlete API.
    Returns a dict with profile data and a Spotrac search link, or None if not found.
    """
    try:
        resp = requests.get(
            ESPN_ATHLETES_URL,
            params={"limit": 5, "active": "true", "q": name},
            timeout=10,
        )
        resp.raise_for_status()
        athletes = resp.json().get("items", [])
        if not athletes:
            return None

        ref_url = athletes[0].get("$ref", "")
        if not ref_url:
            return None

        athlete_resp = requests.get(ref_url, timeout=10)
        athlete_resp.raise_for_status()
        athlete = athlete_resp.json()

        # Resolve team name from $ref if needed
        team_name = ""
        team_ref = athlete.get("team", {})
        if isinstance(team_ref, dict):
            if "$ref" in team_ref:
                try:
                    team_data = requests.get(team_ref["$ref"], timeout=6).json()
                    team_name = team_data.get("displayName", "")
                except Exception:
                    pass
            else:
                team_name = team_ref.get("displayName", "")

        display_name = athlete.get("displayName", name)
        return {
            "name": display_name,
            "position": athlete.get("position", {}).get("abbreviation", ""),
            "team": team_name,
            "jersey": athlete.get("jersey", ""),
            "age": athlete.get("age", ""),
            "experience": athlete.get("experience", {}).get("displayValue", ""),
            "status": athlete.get("status", {}).get("name", ""),
            "height": athlete.get("displayHeight", ""),
            "weight": athlete.get("displayWeight", ""),
            "spotrac_url": "https://www.spotrac.com/nfl/search/?q=" + urllib.parse.quote(display_name),
        }
    except Exception as e:
        print(f"[fetcher] Player lookup failed for '{name}': {e}")
        return None
