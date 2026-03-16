"""
bot.py — NFL Discord Bot
--------------------------
Slash commands:
  /help                — show all commands and usage
  /team <name>         — transactions filtered by team
  /news [team]         — latest NFL headlines, optionally team-filtered
  /player <name>       — NFL player profile with Spotrac link
  /source <source>     — set auto-post source (espn / bluesky / both)
  /interval <minutes>  — set ESPN auto-post check frequency
  /writers [writer]    — view or toggle Bluesky beat writers (with enable/disable all)

Auto-posting:
  ESPN news stories    — every 30 min by default (adjustable via /interval)
  Bluesky beat writers — every 10 min (independent loop)
  Both loops deduplicate against a shared in-memory set (persisted to seen_ids.json) and respect the /source setting.

Setup:
  1. Copy .env.example → .env and fill in your values
  2. pip install -r requirements.txt
  3. SYNC_COMMANDS=1 python bot.py   ← first run to register slash commands
  4. python bot.py                   ← subsequent runs
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from fetcher import get_news, get_transactions, get_all_news, get_player
from filters import is_notable_news
from title_parser import build_structured_title
from bluesky import get_writer_posts, WRITER_HANDLES
from classifier import refresh_notable_players

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("nfl-bot")

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("NEWS_CHANNEL_ID", "0"))
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
SYNC_COMMANDS = os.getenv("SYNC_COMMANDS", "0").strip().lower() in ("1", "true", "yes")
SEEN_FILE = "seen_ids.json"
SETTINGS_FILE = "settings.json"
SEEN_MAX_SIZE = 500

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Writer metadata ───────────────────────────────────────────────────────────
WRITER_DISPLAY = {
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

_WRITER_CHOICES = [
    app_commands.Choice(name="✅ Enable All Writers", value="__all_on__"),
    app_commands.Choice(name="❌ Disable All Writers", value="__all_off__"),
] + [
    app_commands.Choice(name=display, value=handle)
    for handle, display in WRITER_DISPLAY.items()
]


# ── Deduplication helpers ─────────────────────────────────────────────────────
_seen: set[str] = set()


def load_seen() -> None:
    """Load seen IDs from disk into the shared in-memory set."""
    global _seen
    try:
        with open(SEEN_FILE) as f:
            _seen = set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        _seen = set()


def save_seen() -> None:
    """Persist the shared in-memory seen set to disk, trimmed to SEEN_MAX_SIZE."""
    items = list(_seen)
    if len(items) > SEEN_MAX_SIZE:
        items = items[-SEEN_MAX_SIZE:]
    with open(SEEN_FILE, "w") as f:
        json.dump(items, f)


# ── Settings helpers ──────────────────────────────────────────────────────────
def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"source": "both", "disabled_writers": []}


def save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def get_active_handles(settings: dict) -> list[str]:
    disabled = set(settings.get("disabled_writers", []))
    return [h for h in WRITER_HANDLES if h not in disabled]


# ── Permission helpers ────────────────────────────────────────────────────────
def _has_manage_guild(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    if not isinstance(interaction.user, discord.Member):
        return False
    return interaction.user.guild_permissions.manage_guild


# ── Embed builders ────────────────────────────────────────────────────────────
def transaction_embed(item: dict, reason: str = "") -> discord.Embed:
    team = item.get("team") or "NFL"
    embed = discord.Embed(
        title=build_structured_title(item),
        color=discord.Color.from_str("#013369"),  # NFL navy
        timestamp=datetime.now(timezone.utc),
    )
    author_line = f"🏈 {team}" + (f"  ·  {reason}" if reason else "")
    embed.set_author(name=author_line)
    if item.get("date"):
        embed.set_footer(text=item["date"][:10])
    return embed


def bluesky_embed(post: dict) -> discord.Embed:
    embed = discord.Embed(
        description=post["text"],
        color=discord.Color.from_str("#0085FF"),  # Bluesky blue
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(name=f"🦋 {post['author']} (@{post['handle']})")
    if post.get("url"):
        embed.add_field(name="", value=f"[View on Bluesky]({post['url']})", inline=False)
    embed.set_footer(text="Source: Bluesky")
    return embed


def news_story_embed(item: dict, reason: str = "") -> discord.Embed:
    summary = (item.get("summary") or "")[:300]
    embed = discord.Embed(
        title=item.get("title", "NFL News"),
        url=item.get("link") or None,
        description=summary,
        color=discord.Color.from_str("#D50A0A"),
        timestamp=datetime.now(timezone.utc),
    )
    if reason:
        embed.set_author(name=reason)
    embed.set_footer(text="Source: ESPN")
    return embed


def news_embed(items: list[dict], title: str = "📰 Latest NFL News") -> discord.Embed:
    embed = discord.Embed(
        title=title,
        color=discord.Color.from_str("#D50A0A"),  # NFL red
        timestamp=datetime.now(timezone.utc),
    )
    for item in items:
        summary = item.get("summary", "") or ""
        summary = summary[:200] + "…" if len(summary) > 200 else summary
        link = item.get("link") or ""
        value = f"{summary}\n[Read more]({link})" if link else summary or "No summary available."
        embed.add_field(name=item.get("title", "No title"), value=value, inline=False)
    embed.set_footer(text="Source: ESPN")
    return embed


def player_embed(player: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"🏈 {player['name']}",
        color=discord.Color.from_str("#013369"),
        timestamp=datetime.now(timezone.utc),
    )
    if player.get("position"):
        embed.add_field(name="Position", value=player["position"], inline=True)
    if player.get("team"):
        embed.add_field(name="Team", value=player["team"], inline=True)
    if player.get("jersey"):
        embed.add_field(name="Jersey", value=f"#{player['jersey']}", inline=True)
    if player.get("height") or player.get("weight"):
        size = " / ".join(filter(None, [player.get("height"), player.get("weight")]))
        embed.add_field(name="Size", value=size, inline=True)
    if player.get("age"):
        embed.add_field(name="Age", value=str(player["age"]), inline=True)
    if player.get("experience"):
        embed.add_field(name="Experience", value=player["experience"], inline=True)
    if player.get("status"):
        embed.add_field(name="Status", value=player["status"], inline=True)
    if player.get("espn_url"):
        embed.add_field(
            name="Profile",
            value=f"[View on ESPN]({player['espn_url']})",
            inline=False,
        )
    if player.get("headshot"):
        embed.set_thumbnail(url=player["headshot"])
    embed.set_footer(text="Source: ESPN")
    return embed


# ── Startup ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
    if SYNC_COMMANDS:
        try:
            synced = await bot.tree.sync()
            logger.info("Synced %s slash command(s)", len(synced))
        except Exception as e:
            logger.exception("Slash command sync failed: %s", e)
    else:
        logger.debug("SYNC_COMMANDS not set — skipping tree sync")

    load_seen()

    if not Path("notable_players_cache.json").exists():
        await asyncio.to_thread(refresh_notable_players)  # seed on first boot

    if CHANNEL_ID:
        settings = load_settings()
        source = settings.get("source", "both")
        saved_interval = settings.get("espn_interval", CHECK_INTERVAL_MINUTES)
        if saved_interval != CHECK_INTERVAL_MINUTES:
            auto_post_espn.change_interval(minutes=saved_interval)
        auto_post_espn.start()
        auto_post_bluesky.start()
        refresh_player_list.start()
        logger.info("ESPN news loop: every %s min | Bluesky loop: every 10 min", saved_interval)
        logger.info("Active source: %s → channel %s", source, CHANNEL_ID)
    else:
        logger.warning("NEWS_CHANNEL_ID not set — auto-posting disabled")


# ── Scheduled tasks ───────────────────────────────────────────────────────────
@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def auto_post_espn():
    settings = load_settings()
    if settings.get("source") not in ("espn", "both"):
        return

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.warning("[espn] Channel %s not found", CHANNEL_ID)
        return

    all_news = await asyncio.to_thread(get_all_news, 50)

    if not all_news:
        logger.warning("[espn] Feed fetch returned empty — skipping this cycle")
        return

    notable = []
    for item in all_news:
        if item["id"] in _seen:
            continue
        should_post, reason = is_notable_news(item)
        if should_post:
            notable.append((item, reason))

    posted = 0
    for item, reason in notable[:5]:
        try:
            await channel.send(embed=news_story_embed(item, reason))
            _seen.add(item["id"])
            posted += 1
        except discord.HTTPException as e:
            logger.warning("[espn] Send failed: %s", e)

    for item in all_news:
        _seen.add(item["id"])

    save_seen()
    if posted:
        logger.info("[espn] Posted %s news story(s)", posted)
    else:
        logger.debug("[espn] No new notable stories")


@tasks.loop(minutes=10)
async def auto_post_bluesky():
    settings = load_settings()
    if settings.get("source") not in ("bluesky", "both"):
        return

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.warning("[bluesky] Channel %s not found", CHANNEL_ID)
        return

    active_handles = get_active_handles(settings)
    if not active_handles:
        return

    bsky_posts = await asyncio.to_thread(get_writer_posts, active_handles)

    if not bsky_posts:
        logger.warning("[bluesky] Feed fetch returned empty — skipping this cycle")
        return

    posted = 0
    for post in bsky_posts:
        if posted >= 5:
            break
        if post["id"] in _seen:
            continue
        try:
            await channel.send(embed=bluesky_embed(post))
            _seen.add(post["id"])
            posted += 1
        except discord.HTTPException as e:
            logger.warning("[bluesky] Send failed: %s", e)

    for post in bsky_posts:
        _seen.add(post["id"])

    save_seen()
    if posted:
        logger.info("[bluesky] Posted %s post(s)", posted)


@auto_post_espn.before_loop
@auto_post_bluesky.before_loop
async def before_loops():
    await bot.wait_until_ready()


@tasks.loop(hours=24)
async def refresh_player_list():
    """Fire on the 1st of every quarter (Jan, Apr, Jul, Oct) regardless of uptime."""
    now = datetime.now(timezone.utc)
    if now.day == 1 and now.month in (1, 4, 7, 10):
        await asyncio.to_thread(refresh_notable_players)
        logger.info("Quarterly player list refresh complete")


@refresh_player_list.before_loop
async def before_refresh():
    await bot.wait_until_ready()


# ── Slash commands ─────────────────────────────────────────────────────────────
@bot.tree.command(name="help", description="Show all bot commands and usage")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏈 NFL Bot — Command Reference",
        color=discord.Color.from_str("#013369"),
    )
    embed.add_field(
        name="/team `<name>`",
        value="Latest ESPN transactions for a specific team. Accepts full names, cities, or abbreviations (e.g. `Bears`, `CHI`, `Chicago`).",
        inline=False,
    )
    embed.add_field(
        name="/news `[team]`",
        value="Latest NFL headlines from ESPN. Pass a team name to filter (e.g. `/news Bears`).",
        inline=False,
    )
    embed.add_field(
        name="/player `<name>`",
        value="NFL player profile — position, team, size, experience, and a link to their Spotrac contract page.",
        inline=False,
    )
    embed.add_field(
        name="/source `<espn | bluesky | both>`",
        value="Set which source the auto-post loop pulls from. Persists across restarts.",
        inline=False,
    )
    embed.add_field(
        name="/interval `<minutes>`",
        value="Set how often the ESPN loop checks for new news stories (10 / 30 / 60 / 120 min). Persists across restarts.",
        inline=False,
    )
    embed.add_field(
        name="/writers `[writer]`",
        value="View all Bluesky beat writers and their status. Pass a writer to toggle, or choose Enable All / Disable All.",
        inline=False,
    )
    embed.add_field(
        name="/refresh-players",
        value="Force-refresh the notable NFL player list used by the LLM classifier via Perplexity Sonar. Requires Manage Server.",
        inline=False,
    )
    embed.set_footer(text="Auto-post: ESPN interval adjustable · Bluesky every 10 min")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="team", description="Get transactions for a specific NFL team")
@app_commands.describe(team="Team name or abbreviation (e.g. Bears, CHI, 49ers)")
async def cmd_team(interaction: discord.Interaction, team: str):
    await interaction.response.defer()
    items = get_transactions(limit=5, team_filter=team)
    if not items:
        await interaction.followup.send(
            f"⚠️ No recent transactions found for **{team}**. "
            "Try a different spelling or check back later."
        )
        return
    embeds = [transaction_embed(i, "") for i in items]
    await interaction.followup.send(content=f"**Transactions — {team.title()}**", embeds=embeds)


@bot.tree.command(name="news", description="Get the latest NFL news headlines")
@app_commands.describe(team="Optional team filter (e.g. Bears, CHI, Chicago)")
async def cmd_news(interaction: discord.Interaction, team: str | None = None):
    await interaction.response.defer()
    items = get_news(limit=10, team_filter=team)
    if not items:
        msg = (
            f"⚠️ No recent news found for **{team}**. Try a different spelling or check back later."
            if team else
            "⚠️ No news found. Try again shortly."
        )
        await interaction.followup.send(msg)
        return
    title = f"📰 Latest NFL News — {team.title()}" if team else "📰 Latest NFL News"
    await interaction.followup.send(embed=news_embed(items[:5], title=title))


@bot.tree.command(name="source", description="Set the auto-post news source")
@app_commands.describe(source="News source to use for auto-posting")
@app_commands.choices(source=[
    app_commands.Choice(name="ESPN transactions", value="espn"),
    app_commands.Choice(name="Bluesky beat writers", value="bluesky"),
    app_commands.Choice(name="Both", value="both"),
])
async def cmd_source(interaction: discord.Interaction, source: str):
    if not _has_manage_guild(interaction):
        await interaction.response.send_message(
            "⚠️ You need Manage Server permission to change the source.", ephemeral=True
        )
        return
    settings = load_settings()
    settings["source"] = source
    save_settings(settings)
    labels = {"espn": "ESPN", "bluesky": "Bluesky", "both": "Both (ESPN + Bluesky)"}
    await interaction.response.send_message(
        f"✅ Auto-post source set to **{labels[source]}**.", ephemeral=True
    )


@bot.tree.command(name="interval", description="Set how often the ESPN auto-post loop checks for new transactions")
@app_commands.describe(minutes="Check interval in minutes")
@app_commands.choices(minutes=[
    app_commands.Choice(name="1 minute (debug)", value=1),
    app_commands.Choice(name="10 minutes", value=10),
    app_commands.Choice(name="30 minutes", value=30),
    app_commands.Choice(name="60 minutes", value=60),
    app_commands.Choice(name="120 minutes", value=120),
])
async def cmd_interval(interaction: discord.Interaction, minutes: int):
    if not _has_manage_guild(interaction):
        await interaction.response.send_message(
            "⚠️ You need Manage Server permission to change the interval.", ephemeral=True
        )
        return
    settings = load_settings()
    settings["espn_interval"] = minutes
    save_settings(settings)
    auto_post_espn.change_interval(minutes=minutes)
    await interaction.response.send_message(
        f"✅ ESPN auto-post interval set to **{minutes} minutes**.", ephemeral=True
    )


@bot.tree.command(name="writers", description="View or toggle Bluesky beat writers")
@app_commands.describe(writer="Writer to toggle, Enable All, or Disable All (omit to view all)")
@app_commands.choices(writer=_WRITER_CHOICES)
async def cmd_writers(interaction: discord.Interaction, writer: str | None = None):
    settings = load_settings()
    disabled = set(settings.get("disabled_writers", []))

    # View all — no permission required
    if writer is None:
        embed = discord.Embed(
            title="🦋 Bluesky Beat Writers",
            color=discord.Color.from_str("#0085FF"),
        )
        lines = []
        for handle in WRITER_HANDLES:
            status = "❌" if handle in disabled else "✅"
            display = WRITER_DISPLAY.get(handle, handle)
            lines.append(f"{status} {display}")
        embed.description = "\n".join(lines)
        embed.set_footer(text="Use /writers <name> to toggle · Enable All / Disable All available")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # All toggle actions require Manage Server
    if not _has_manage_guild(interaction):
        await interaction.response.send_message(
            "⚠️ You need Manage Server permission to toggle writers.", ephemeral=True
        )
        return

    # Enable / disable all
    if writer == "__all_on__":
        settings["disabled_writers"] = []
        save_settings(settings)
        await interaction.response.send_message("✅ All writers enabled.", ephemeral=True)
        return
    if writer == "__all_off__":
        settings["disabled_writers"] = list(WRITER_HANDLES)
        save_settings(settings)
        await interaction.response.send_message("❌ All writers disabled.", ephemeral=True)
        return

    # Toggle individual writer
    if writer in disabled:
        disabled.discard(writer)
        action = "enabled"
    else:
        disabled.add(writer)
        action = "disabled"

    settings["disabled_writers"] = list(disabled)
    save_settings(settings)
    display = WRITER_DISPLAY.get(writer, writer)
    icon = "✅" if action == "enabled" else "❌"
    await interaction.response.send_message(
        f"{icon} **{display}** has been **{action}**.", ephemeral=True
    )


@bot.tree.command(name="player", description="Look up an NFL player profile")
@app_commands.describe(name="Player name (e.g. Ja'Marr Chase, Patrick Mahomes)")
async def cmd_player(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    player = get_player(name)
    if not player:
        await interaction.followup.send(
            f"⚠️ Could not find **{name}**. Check the spelling and try again.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(embed=player_embed(player))


@bot.tree.command(name="refresh-players", description="Force-refresh the notable NFL player list via web research")
async def cmd_refresh_players(interaction: discord.Interaction):
    if not _has_manage_guild(interaction):
        await interaction.response.send_message(
            "⚠️ You need Manage Server permission to refresh the player list.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    await asyncio.to_thread(refresh_notable_players)

    try:
        cache = json.loads(Path("notable_players_cache.json").read_text())
        players = cache["players"]
        updated_at = cache["updated_at"][:10]
        player_text = ", ".join(sorted(players))
        chunks = [player_text[i:i+1000] for i in range(0, len(player_text), 1000)]
        embed = discord.Embed(
            title="🏈 Notable NFL Players — Updated",
            description=f"Refreshed via Perplexity Sonar on {updated_at}. {len(players)} players.",
            color=discord.Color.green(),
        )
        for i, chunk in enumerate(chunks):
            embed.add_field(name=f"Players ({i+1}/{len(chunks)})", value=chunk, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Refresh failed: {e}", ephemeral=True)


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN is not set in your .env file")
    bot.run(TOKEN)
