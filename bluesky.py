"""
bluesky.py — Fetches recent posts from NFL beat writers on Bluesky.
Uses Bluesky's public REST API (no authentication required).

Story deduplication:
  When multiple writers post about the same story (e.g. a trade), only the
  earliest post is kept. Two posts are treated as the same story if they share
  at least one named entity (capitalized 2+ word sequence) and were posted
  within STORY_WINDOW_MINUTES of each other.
"""

import hashlib
import re
import requests
from datetime import datetime, timezone

BLUESKY_API = "https://public.api.bsky.app/xrpc"

# Beat writer handles
WRITER_HANDLES: list[str] = [
    "rapsheet.bsky.social",        # Ian Rapoport — NFL Network
    "diannarussini.bsky.social",   # Dianna Russini — The Athletic
    "tednguyen.bsky.social",       # Ted Nguyen — The Athletic
    "miketanier.bsky.social",      # Mike Tanier — Football Outsiders / Freelance
    "kevinseifert.bsky.social",    # Kevin Seifert — ESPN
    "wyche89.bsky.social",         # Steve Wyche — NFL Network
    "agetzenberg.bsky.social",     # Alaina Getzenberg — ESPN
    "ml-j.bsky.social",            # Marcel Louis-Jacques — ESPN
    "profootballtalk.bsky.social", # ProFootballTalk — NBC Sports
    "jamisonhensley.bsky.social",  # Jamison Hensley — ESPN
    "jennalaine.bsky.social",      # Jenna Laine — ESPN
    "tompelissero.bsky.social",    # Tom Pelissero — NFL Network
]

# ── Relevance filtering ───────────────────────────────────────────────────────

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

# Weak signals: can also appear in opinion or analysis pieces
_WEAK_KEYWORDS = frozenset({
    "contract", "trade", "draft", "reserve", "sign", "free agent", "cut",
})


def _build_kw_re(kw_set: frozenset) -> re.Pattern:
    alts = "|".join(re.escape(k) for k in sorted(kw_set, key=len, reverse=True))
    return re.compile(r"\b(?:" + alts + r")\b", re.IGNORECASE)


_STRONG_KW_RE = _build_kw_re(_STRONG_KEYWORDS)
_WEAK_KW_RE   = _build_kw_re(_WEAK_KEYWORDS)

# Sourcing or financial language that corroborates a weak-keyword match
_SOURCE_RE = re.compile(
    r"sources?|per\s|\$\d|million|\baav\b|agreed\s+to|\d-year|one-year|multi-year",
    re.IGNORECASE,
)

# Hypothetical/opinion phrasing — disqualifies a post even with strong keywords present
_OPINION_RE = re.compile(
    r"\b(?:should|could|would|might)\s+(?:have\s+)?(?:traded?|cuts?|signed?|drafted?|released?|waived?|tagged)\b",
    re.IGNORECASE,
)

_NAME_RE = re.compile(r"\b([A-Z][a-z']+(?:\s[A-Z][a-z']+)+)\b")
STORY_WINDOW_MINUTES = 30  # Posts within this window sharing a name = same story


def _is_nfl_relevant(text: str) -> bool:
    """Return True if this post looks like actual NFL transaction news, not opinion or analysis."""
    # Disqualify hypothetical/opinion phrasing up front
    if _OPINION_RE.search(text):
        return False
    # Strong keyword → pass immediately
    if _STRONG_KW_RE.search(text):
        return True
    # Weak keyword only → require sourcing or financial corroboration
    if _WEAK_KW_RE.search(text):
        return bool(_SOURCE_RE.search(text))
    return False


def _extract_name_tokens(text: str) -> frozenset[str]:
    """Extract multi-word capitalized sequences (likely player/team names)."""
    return frozenset(_NAME_RE.findall(text))


def _parse_timestamp(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _deduplicate_stories(posts: list[dict]) -> list[dict]:
    """
    Filter out posts that cover the same story as an earlier post.
    Sorts by timestamp so the first writer to post wins.
    Two posts are the same story if they share at least one named entity
    and were posted within STORY_WINDOW_MINUTES of each other.
    """
    sorted_posts = sorted(posts, key=lambda p: _parse_timestamp(p["timestamp"]))
    seen_stories: list[tuple[frozenset[str], datetime]] = []
    result = []

    for post in sorted_posts:
        post_ts = _parse_timestamp(post["timestamp"])
        tokens = _extract_name_tokens(post["text"])

        if not tokens:
            # No extractable names — can't fingerprint, let it through
            result.append(post)
            continue

        is_duplicate = False
        for story_tokens, story_ts in seen_stories:
            if abs((post_ts - story_ts).total_seconds()) > STORY_WINDOW_MINUTES * 60:
                continue
            if tokens & story_tokens:  # shared name entity
                is_duplicate = True
                break

        if not is_duplicate:
            result.append(post)
            seen_stories.append((tokens, post_ts))

    return result


def get_writer_posts(
    handles: list[str] | None = None,
    limit_per_writer: int = 5,
) -> list[dict]:
    """
    Fetch recent posts from each beat writer handle.
    Returns list of dicts: {id, author, handle, text, url, timestamp}
    Only includes posts that pass the NFL relevance keyword filter.
    """
    if handles is None:
        handles = WRITER_HANDLES

    posts = []
    for handle in handles:
        try:
            resp = requests.get(
                f"{BLUESKY_API}/app.bsky.feed.getAuthorFeed",
                params={
                    "actor": handle,
                    "limit": limit_per_writer,
                    "filter": "posts_no_replies",
                },
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[bluesky] Failed to fetch posts for {handle}: {e}")
            continue

        for entry in data.get("feed", []):
            # Skip reposts — reason field is set by Bluesky on reposted entries,
            # meaning the original author is someone other than the tracked writer
            if entry.get("reason"):
                continue

            post = entry.get("post", {})
            record = post.get("record", {})
            text = record.get("text", "").strip()

            if not text or not _is_nfl_relevant(text):
                continue

            uri = post.get("uri", "")
            uid = hashlib.md5(uri.encode()).hexdigest()
            author_info = post.get("author", {})
            display_name = author_info.get("displayName", handle)
            at_handle = author_info.get("handle", handle)

            # Convert AT URI → web URL
            # at://did:plc:xxx/app.bsky.feed.post/rkey → bsky.app/profile/{handle}/post/{rkey}
            rkey = uri.rsplit("/", 1)[-1] if uri else ""
            web_url = f"https://bsky.app/profile/{at_handle}/post/{rkey}" if rkey else ""

            posts.append({
                "id": uid,
                "author": display_name,
                "handle": at_handle,
                "text": text,
                "url": web_url,
                "timestamp": record.get("createdAt", ""),
            })

    return _deduplicate_stories(posts)
