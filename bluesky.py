"""
bluesky.py — Fetches recent posts from NFL beat writers on Bluesky.
Uses Bluesky's public REST API (no authentication required).

Relevance is decided by the shared weighted scorer in scoring.py; each
returned post carries its score and reason tags for the caller to rank on.

Story deduplication:
  When multiple writers post about the same story (e.g. a trade), only the
  earliest post is kept. Two posts are treated as the same story if they share
  at least one named entity (capitalized 2+ word sequence) and were posted
  within STORY_WINDOW_MINUTES of each other.
"""

import hashlib
import logging
import re
import requests
from datetime import datetime, timezone

from scoring import score_text, POST_THRESHOLD

logger = logging.getLogger("nfl-bot.bluesky")

BLUESKY_API = "https://public.api.bsky.app/xrpc"

# Beat writers — handle → display name. Single source of truth for the
# fetcher, the /writers command, and the /settings panel.
WRITERS: dict[str, str] = {
    "rapsheet.bsky.social":        "Ian Rapoport (NFL Network)",
    "diannarussini.bsky.social":   "Dianna Russini (The Athletic)",
    "tednguyen.bsky.social":       "Ted Nguyen (The Athletic)",
    "miketanier.bsky.social":      "Mike Tanier (Freelance)",
    "kevinseifert.bsky.social":    "Kevin Seifert (ESPN)",
    "wyche89.bsky.social":         "Steve Wyche (NFL Network)",
    "agetzenberg.bsky.social":     "Alaina Getzenberg (ESPN)",
    "ml-j.bsky.social":            "Marcel Louis-Jacques (ESPN)",
    "profootballtalk.bsky.social": "ProFootballTalk (NBC Sports)",
    "jamisonhensley.bsky.social":  "Jamison Hensley (ESPN)",
    "jennalaine.bsky.social":      "Jenna Laine (ESPN)",
    "tompelissero.bsky.social":    "Tom Pelissero (NFL Network)",
}

WRITER_HANDLES: list[str] = list(WRITERS)

_NAME_RE = re.compile(r"\b([A-Z][a-z']+(?:\s[A-Z][a-z']+)+)\b")
STORY_WINDOW_MINUTES = 30  # Posts within this window sharing a name = same story


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
    Returns list of dicts: {id, author, handle, text, url, timestamp, score, reasons}
    Only includes posts scoring at or above POST_THRESHOLD.
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
            logger.warning("Failed to fetch posts for %s: %s", handle, e)
            continue

        for entry in data.get("feed", []):
            # Skip reposts — reason field is set by Bluesky on reposted entries,
            # meaning the original author is someone other than the tracked writer
            if entry.get("reason"):
                continue

            post = entry.get("post", {})
            record = post.get("record", {})
            text = record.get("text", "").strip()

            if not text:
                continue
            score, reasons = score_text(text)
            if score < POST_THRESHOLD:
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
                "score": score,
                "reasons": reasons,
            })

    return _deduplicate_stories(posts)
