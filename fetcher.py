"""
fetcher.py — pulls NFL transactions and news from ESPN's public APIs and RSS feed.
No API key required.
"""

import hashlib
import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
import requests

logger = logging.getLogger("nfl-bot.fetcher")

_ESPN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

ESPN_TRANSACTIONS_URL = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/transactions"
    "?limit=50"
)
ESPN_NEWS_RSS = "https://www.espn.com/espn/rss/nfl/news"
ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"

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
        resp = requests.get(ESPN_TRANSACTIONS_URL, headers=_ESPN_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("[espn] Transactions fetch OK — HTTP %s", resp.status_code)
    except Exception as e:
        logger.warning("[espn] Transaction fetch failed: %s", e)
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
        resp = requests.get(ESPN_TRANSACTIONS_URL, headers=_ESPN_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("[espn] Transactions fetch OK — HTTP %s", resp.status_code)
    except Exception as e:
        logger.warning("[espn] Transaction fetch failed: %s", e)
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


def _fetch_rss_feed() -> feedparser.FeedParserDict | None:
    """
    Fetch ESPN's NFL RSS feed using requests (controlled timeout + headers),
    then parse the raw content offline via feedparser. Returns None on failure.
    """
    try:
        resp = requests.get(ESPN_NEWS_RSS, headers=_ESPN_HEADERS, timeout=15)
        resp.raise_for_status()
        if not resp.content:
            logger.warning("[espn] News RSS returned empty body (HTTP %s)", resp.status_code)
            return None
        feed = feedparser.parse(io.BytesIO(resp.content))
        logger.info("[espn] News RSS fetch OK — HTTP %s, %d entries", resp.status_code, len(feed.entries))
        return feed
    except Exception as e:
        logger.warning("[espn] News RSS fetch failed: %s", e)
        return None


def get_all_news(limit: int = 50) -> list[dict]:
    """
    Fetch ESPN NFL news RSS items with stable IDs.
    Used by the auto-post loop — no filtering applied.
    """
    feed = _fetch_rss_feed()
    if feed is None:
        return []
    try:
        items = []
        for entry in feed.entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            summary = entry.get("summary", "")
            uid = hashlib.md5((link or title).encode()).hexdigest()
            items.append({
                "id": uid,
                "title": title,
                "link": link,
                "summary": summary,
            })
            if len(items) >= limit:
                break
        return items
    except Exception as e:
        logger.warning("[espn] News RSS parse failed: %s", e)
        return []


def get_news(limit: int = 5, team_filter: str | None = None) -> list[dict]:
    """
    Fetch latest NFL headlines from ESPN's RSS feed.
    Optionally filter by team name/abbreviation (client-side).
    Returns a list of dicts: {title, link, summary}
    """
    feed = _fetch_rss_feed()
    if feed is None:
        return []
    try:
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
        logger.warning("[espn] News RSS parse failed: %s", e)
        return []


def get_player(name: str) -> dict | None:
    """
    Look up an NFL player by name by searching all 32 team rosters in parallel.
    ESPN's athlete search API ignores the name query, so roster search is the
    only reliable approach. Returns a dict with profile data, headshot URL, and
    a Spotrac search link, or None if not found.
    """
    try:
        teams_resp = requests.get(ESPN_TEAMS_URL, headers=_ESPN_HEADERS, params={"limit": 32}, timeout=10)
        teams_resp.raise_for_status()
        teams = [
            (t["team"]["id"], t["team"]["displayName"])
            for t in teams_resp.json().get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
        ]

        name_lower = name.lower().strip()

        def fetch_and_search(team_id, team_name):
            try:
                resp = requests.get(
                    f"{ESPN_TEAMS_URL}/{team_id}/roster",
                    headers=_ESPN_HEADERS,
                    timeout=10,
                )
                resp.raise_for_status()
                for group in resp.json().get("athletes", []):
                    for a in group.get("items", []):
                        if name_lower in a.get("fullName", "").lower():
                            a["_team_name"] = team_name
                            return a
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(fetch_and_search, tid, tname) for tid, tname in teams]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    a = result
                    display_name = a.get("displayName", name)
                    exp_years = a.get("experience", {}).get("years")
                    experience = f"{exp_years} yr{'s' if exp_years != 1 else ''}" if exp_years is not None else ""
                    return {
                        "name": display_name,
                        "position": a.get("position", {}).get("abbreviation", ""),
                        "team": a.get("_team_name", ""),
                        "jersey": a.get("jersey", ""),
                        "age": a.get("age", ""),
                        "experience": experience,
                        "status": a.get("status", {}).get("name", ""),
                        "height": a.get("displayHeight", ""),
                        "weight": a.get("displayWeight", ""),
                        "headshot": a.get("headshot", {}).get("href", ""),
                        "espn_url": f"https://www.espn.com/nfl/player/_/id/{a.get('id', '')}",
                    }

        return None
    except Exception as e:
        logger.warning("[espn] Player lookup failed for '%s': %s", name, e)
        return None
