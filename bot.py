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
  /settings            — interactive dashboard for source, interval, and writers

Auto-posting:
  ESPN news stories    — every 30 min by default (adjustable via /interval)
  Bluesky beat writers — every 10 min (independent loop)
  Both loops deduplicate against seen_ids.json and respect the /source setting.

Setup:
  1. Copy .env.example → .env and fill in your values
  2. pip install -r requirements.txt
  3. SYNC_COMMANDS=1 python bot.py   ← first run to register slash commands
  4. python bot.py                   ← subsequent runs
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from fetcher import get_news, get_transactions, get_all_news, get_player
from scoring import score_text, POST_THRESHOLD
from storage import load_seen, save_seen, load_settings, save_settings
from teams import get_team_branding, identify_team
from title_parser import build_structured_title
from bluesky import get_writer_posts, WRITERS, WRITER_HANDLES

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

# Serializes the load → post → save cycle of both auto-post loops so a slow
# cycle in one loop can't overwrite the seen-state written by the other.
seen_lock = asyncio.Lock()


# ── Bot setup ─────────────────────────────────────────────────────────────────
class NFLBot(commands.Bot):
    async def setup_hook(self) -> None:
        """Runs once after login (unlike on_ready, which fires on every reconnect)."""
        if SYNC_COMMANDS:
            try:
                synced = await self.tree.sync()
                logger.info("Synced %s slash command(s)", len(synced))
            except Exception:
                logger.exception("Slash command sync failed")
        else:
            logger.debug("SYNC_COMMANDS not set — skipping tree sync")

        # Warm the team branding cache off-thread so the first embed build
        # doesn't block the event loop on an HTTP fetch.
        await asyncio.to_thread(get_team_branding, "bears")

        if CHANNEL_ID:
            settings = load_settings()
            saved_interval = settings.get("espn_interval", CHECK_INTERVAL_MINUTES)
            if saved_interval != CHECK_INTERVAL_MINUTES:
                auto_post_espn.change_interval(minutes=saved_interval)
            auto_post_espn.start()
            auto_post_bluesky.start()
            logger.info("ESPN news loop: every %s min | Bluesky loop: every 10 min", saved_interval)
            logger.info("Active source: %s → channel %s", settings.get("source", "both"), CHANNEL_ID)
        else:
            logger.warning("NEWS_CHANNEL_ID not set — auto-posting disabled")


intents = discord.Intents.default()
bot = NFLBot(command_prefix="!", intents=intents)

# ── Writer metadata ───────────────────────────────────────────────────────────
_WRITER_CHOICES = [
    app_commands.Choice(name="✅ Enable All Writers", value="__all_on__"),
    app_commands.Choice(name="❌ Disable All Writers", value="__all_off__"),
] + [
    app_commands.Choice(name=display, value=handle)
    for handle, display in WRITERS.items()
]


# ── Settings helpers ──────────────────────────────────────────────────────────
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
def link_view(label: str, url: str) -> discord.ui.View | None:
    """A view holding a single URL button. URL-only views are stateless, so
    they survive bot restarts without any persistence handling."""
    if not url:
        return None
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=label, url=url))
    return view


def transaction_embed(item: dict, reason: str = "") -> discord.Embed:
    team = item.get("team") or "NFL"
    color, logo = get_team_branding(team)
    embed = discord.Embed(
        title=build_structured_title(item),
        color=discord.Color.from_str(color),
        timestamp=datetime.now(timezone.utc),
    )
    author_line = f"🏈 {team}" + (f"  ·  {reason}" if reason else "")
    embed.set_author(name=author_line)
    if logo:
        embed.set_thumbnail(url=logo)
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
    embed.set_footer(text="Source: Bluesky")
    return embed


def news_story_embed(item: dict, reason: str = "") -> discord.Embed:
    summary = (item.get("summary") or "")[:300]
    team = identify_team(item.get("title", "") + " " + summary)
    color, logo = get_team_branding(team) if team else ("#D50A0A", "")
    embed = discord.Embed(
        title=item.get("title", "NFL News"),
        url=item.get("link") or None,
        description=summary,
        color=discord.Color.from_str(color),
        timestamp=datetime.now(timezone.utc),
    )
    if reason:
        embed.set_author(name=reason)
    if logo:
        embed.set_thumbnail(url=logo)
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

    async with seen_lock:
        seen, seen_list = load_seen()

        scored = []
        for item in all_news:
            if item["id"] in seen:
                continue
            score, reasons = score_text(
                (item.get("title", "") + " " + item.get("summary", "")).strip()
            )
            scored.append((score, item, reasons))
        scored.sort(key=lambda entry: entry[0], reverse=True)

        posted = 0
        for score, item, reasons in scored:
            if score < POST_THRESHOLD:
                # Not worth posting — mark seen so it's never reconsidered.
                seen.add(item["id"])
                seen_list.append(item["id"])
                continue
            if posted >= 5:
                # Above threshold but beyond the per-cycle cap — leave unseen
                # so it posts next cycle instead of being silently dropped.
                continue
            try:
                reason = " · ".join(reasons[:3])
                kwargs = {"embed": news_story_embed(item, reason)}
                view = link_view("Read on ESPN", item.get("link", ""))
                if view:
                    kwargs["view"] = view
                await channel.send(**kwargs)
                seen.add(item["id"])
                seen_list.append(item["id"])
                posted += 1
            except discord.HTTPException as e:
                logger.warning("[espn] Send failed: %s", e)

        save_seen(seen_list)
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

    async with seen_lock:
        seen, seen_list = load_seen()

        # All fetched posts already cleared POST_THRESHOLD in bluesky.py.
        # Pick the 5 highest-scored unseen posts, then post chronologically;
        # the rest stay unseen and get another shot next cycle.
        unseen = [p for p in bsky_posts if p["id"] not in seen]
        selected = sorted(unseen, key=lambda p: p.get("score", 0), reverse=True)[:5]
        selected.sort(key=lambda p: p.get("timestamp", ""))

        posted = 0
        for post in selected:
            try:
                kwargs = {"embed": bluesky_embed(post)}
                view = link_view("View on Bluesky", post.get("url", ""))
                if view:
                    kwargs["view"] = view
                await channel.send(**kwargs)
                seen.add(post["id"])
                seen_list.append(post["id"])
                posted += 1
            except discord.HTTPException as e:
                logger.warning("[bluesky] Send failed: %s", e)

        save_seen(seen_list)
    if posted:
        logger.info("[bluesky] Posted %s post(s)", posted)


@auto_post_espn.before_loop
@auto_post_bluesky.before_loop
async def before_loops():
    await bot.wait_until_ready()


# ── /settings panel ───────────────────────────────────────────────────────────
_SOURCE_LABELS = {"espn": "ESPN", "bluesky": "Bluesky", "both": "Both (ESPN + Bluesky)"}
_INTERVAL_CHOICES = (10, 30, 60, 120)


def settings_embed(settings: dict) -> discord.Embed:
    source = settings.get("source", "both")
    interval = settings.get("espn_interval", CHECK_INTERVAL_MINUTES)
    disabled = set(settings.get("disabled_writers", []))
    enabled_count = len(WRITER_HANDLES) - len(disabled & set(WRITER_HANDLES))
    embed = discord.Embed(
        title="⚙️ NFL Bot Settings",
        color=discord.Color.from_str("#013369"),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Source", value=_SOURCE_LABELS.get(source, source), inline=True)
    embed.add_field(name="ESPN interval", value=f"{interval} min", inline=True)
    embed.add_field(
        name="Writers",
        value=f"{enabled_count}/{len(WRITER_HANDLES)} enabled",
        inline=True,
    )
    embed.set_footer(text="Changes apply immediately and persist across restarts")
    return embed


class SourceSelect(discord.ui.Select):
    def __init__(self, settings: dict):
        current = settings.get("source", "both")
        options = [
            discord.SelectOption(label=label, value=value, default=value == current)
            for value, label in _SOURCE_LABELS.items()
        ]
        super().__init__(placeholder="Auto-post source", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        settings = load_settings()
        settings["source"] = self.values[0]
        save_settings(settings)
        await interaction.response.edit_message(
            embed=settings_embed(settings), view=SettingsView(settings)
        )


class IntervalSelect(discord.ui.Select):
    def __init__(self, settings: dict):
        current = settings.get("espn_interval", CHECK_INTERVAL_MINUTES)
        options = [
            discord.SelectOption(label=f"{m} minutes", value=str(m), default=m == current)
            for m in _INTERVAL_CHOICES
        ]
        super().__init__(placeholder="ESPN check interval", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        minutes = int(self.values[0])
        settings = load_settings()
        settings["espn_interval"] = minutes
        save_settings(settings)
        auto_post_espn.change_interval(minutes=minutes)
        await interaction.response.edit_message(
            embed=settings_embed(settings), view=SettingsView(settings)
        )


class WriterSelect(discord.ui.Select):
    def __init__(self, settings: dict):
        disabled = set(settings.get("disabled_writers", []))
        options = [
            discord.SelectOption(label=display, value=handle, default=handle not in disabled)
            for handle, display in WRITERS.items()
        ]
        super().__init__(
            placeholder="Enabled Bluesky writers",
            options=options,
            min_values=0,
            max_values=len(options),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        enabled = set(self.values)
        settings = load_settings()
        settings["disabled_writers"] = [h for h in WRITER_HANDLES if h not in enabled]
        save_settings(settings)
        await interaction.response.edit_message(
            embed=settings_embed(settings), view=SettingsView(settings)
        )


class SettingsView(discord.ui.View):
    """Ephemeral dashboard — rebuilt on every change so the selects always
    show the persisted state as their defaults."""

    def __init__(self, settings: dict):
        super().__init__(timeout=600)
        self.add_item(SourceSelect(settings))
        self.add_item(IntervalSelect(settings))
        self.add_item(WriterSelect(settings))


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
        name="/settings",
        value="Open an interactive dashboard with dropdowns for source, ESPN interval, and enabled writers (Manage Server required).",
        inline=False,
    )
    embed.set_footer(text="Auto-post: ESPN interval adjustable · Bluesky every 10 min")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="team", description="Get transactions for a specific NFL team")
@app_commands.describe(team="Team name or abbreviation (e.g. Bears, CHI, 49ers)")
async def cmd_team(interaction: discord.Interaction, team: str):
    await interaction.response.defer()
    items = await asyncio.to_thread(get_transactions, limit=5, team_filter=team)
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
    items = await asyncio.to_thread(get_news, limit=10, team_filter=team)
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
            display = WRITERS.get(handle, handle)
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
    display = WRITERS.get(writer, writer)
    icon = "✅" if action == "enabled" else "❌"
    await interaction.response.send_message(
        f"{icon} **{display}** has been **{action}**.", ephemeral=True
    )


@bot.tree.command(name="settings", description="Open the bot settings dashboard")
async def cmd_settings(interaction: discord.Interaction):
    if not _has_manage_guild(interaction):
        await interaction.response.send_message(
            "⚠️ You need Manage Server permission to change settings.", ephemeral=True
        )
        return
    settings = load_settings()
    await interaction.response.send_message(
        embed=settings_embed(settings), view=SettingsView(settings), ephemeral=True
    )


@bot.tree.command(name="player", description="Look up an NFL player profile")
@app_commands.describe(name="Player name (e.g. Ja'Marr Chase, Patrick Mahomes)")
async def cmd_player(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    player = await asyncio.to_thread(get_player, name)
    if not player:
        await interaction.followup.send(
            f"⚠️ Could not find **{name}**. Check the spelling and try again.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(embed=player_embed(player))


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN is not set in your .env file")
    bot.run(TOKEN)
