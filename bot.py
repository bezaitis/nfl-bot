"""
bot.py — NFL Discord Bot
--------------------------
Slash commands:
  /help                — show all commands and usage
  /transactions        — latest NFL transactions
  /team <name>         — transactions filtered by team
  /news                — latest NFL headlines
  /source <source>     — set auto-post source (espn / bluesky / both)
  /writers [writer]    — view or toggle individual Bluesky beat writers

Auto-posting:
  ESPN transactions    — every 30 min (or CHECK_INTERVAL_MINUTES)
  Bluesky beat writers — every 10 min (independent loop)
  Both loops deduplicate against seen_ids.json and respect the /source setting.

Setup:
  1. Copy .env.example → .env and fill in your values
  2. pip install -r requirements.txt
  3. python bot.py
"""

import json
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from fetcher import get_news, get_transactions, get_all_transactions
from filters import is_notable_transaction
from title_parser import build_structured_title
from bluesky import get_writer_posts, WRITER_HANDLES

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("NEWS_CHANNEL_ID", "0"))
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
SEEN_FILE = "seen_ids.json"
SETTINGS_FILE = "settings.json"

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Writer metadata ───────────────────────────────────────────────────────────
# Display names for each handle — used in /writers embed and choices
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
    app_commands.Choice(name=display, value=handle)
    for handle, display in WRITER_DISPLAY.items()
]


# ── Deduplication helpers ─────────────────────────────────────────────────────
def load_seen() -> set[str]:
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen: set[str]) -> None:
    # Keep only the most recent 500 IDs to avoid unbounded growth
    trimmed = list(seen)[-500:]
    with open(SEEN_FILE, "w") as f:
        json.dump(trimmed, f)


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


def news_embed(items: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title="📰 Latest NFL News",
        color=discord.Color.from_str("#D50A0A"),  # NFL red
        timestamp=datetime.now(timezone.utc),
    )
    for item in items:
        summary = item["summary"][:200] + "…" if len(item["summary"]) > 200 else item["summary"]
        embed.add_field(
            name=item["title"],
            value=f"{summary}\n[Read more]({item['link']})" if item["link"] else summary,
            inline=False,
        )
    embed.set_footer(text="Source: ESPN")
    return embed


# ── Startup ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"⚡ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Sync failed: {e}")

    if CHANNEL_ID:
        settings = load_settings()
        source = settings.get("source", "both")
        auto_post_espn.start()
        auto_post_bluesky.start()
        print(f"⏱  ESPN loop: every {CHECK_INTERVAL_MINUTES} min | Bluesky loop: every 10 min")
        print(f"📡 Active source: {source} → channel {CHANNEL_ID}")
    else:
        print("⚠️  NEWS_CHANNEL_ID not set — auto-posting disabled")


# ── Scheduled tasks ───────────────────────────────────────────────────────────
@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def auto_post_espn():
    settings = load_settings()
    if settings.get("source") not in ("espn", "both"):
        return

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"[espn] Channel {CHANNEL_ID} not found")
        return

    seen = load_seen()
    all_transactions = get_all_transactions(limit=50)

    notable = []
    for t in all_transactions:
        if t["id"] in seen:
            continue
        should_post, reason = is_notable_transaction(t)
        if should_post:
            notable.append((t, reason))

    posted = 0
    for item, reason in notable[:5]:
        try:
            await channel.send(embed=transaction_embed(item, reason))
            seen.add(item["id"])
            posted += 1
        except discord.HTTPException as e:
            print(f"[espn] Send failed: {e}")

    # Mark all fetched transactions seen to avoid re-evaluating next cycle
    for t in all_transactions:
        seen.add(t["id"])

    save_seen(seen)
    if posted:
        print(f"[espn] Posted {posted} transaction(s)")
    else:
        print("[espn] No new notable transactions")


@tasks.loop(minutes=10)
async def auto_post_bluesky():
    settings = load_settings()
    if settings.get("source") not in ("bluesky", "both"):
        return

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"[bluesky] Channel {CHANNEL_ID} not found")
        return

    active_handles = get_active_handles(settings)
    if not active_handles:
        return

    seen = load_seen()
    bsky_posts = get_writer_posts(handles=active_handles)

    posted = 0
    for post in bsky_posts:
        if posted >= 5:
            break
        if post["id"] in seen:
            continue
        try:
            await channel.send(embed=bluesky_embed(post))
            seen.add(post["id"])
            posted += 1
        except discord.HTTPException as e:
            print(f"[bluesky] Send failed: {e}")

    for post in bsky_posts:
        seen.add(post["id"])

    save_seen(seen)
    if posted:
        print(f"[bluesky] Posted {posted} post(s)")


@auto_post_espn.before_loop
@auto_post_bluesky.before_loop
async def before_loops():
    await bot.wait_until_ready()


# ── Slash commands ─────────────────────────────────────────────────────────────
@bot.tree.command(name="help", description="Show all bot commands and usage")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏈 NFL Bot — Command Reference",
        color=discord.Color.from_str("#013369"),
    )
    embed.add_field(
        name="/transactions",
        value="Latest 5 NFL transactions from ESPN.",
        inline=False,
    )
    embed.add_field(
        name="/team `<name>`",
        value="Transactions for a specific team. Accepts full names, cities, or abbreviations (e.g. `Bears`, `CHI`, `Chicago`).",
        inline=False,
    )
    embed.add_field(
        name="/news",
        value="Latest 5 NFL headlines from ESPN.",
        inline=False,
    )
    embed.add_field(
        name="/source `<espn | bluesky | both>`",
        value="Set which source the auto-post loop pulls from. Persists across restarts.",
        inline=False,
    )
    embed.add_field(
        name="/writers `[writer]`",
        value="View all Bluesky beat writers and their status. Pass a writer to toggle them on or off.",
        inline=False,
    )
    embed.set_footer(text=f"Auto-post: ESPN every {CHECK_INTERVAL_MINUTES} min · Bluesky every 10 min")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="transactions", description="Get the latest NFL transactions")
async def cmd_transactions(interaction: discord.Interaction):
    await interaction.response.defer()
    items = get_transactions(limit=5)
    if not items:
        await interaction.followup.send("⚠️ No recent transactions found. Try again shortly.")
        return
    embeds = [transaction_embed(i, "") for i in items]
    await interaction.followup.send(content="**Latest NFL Transactions**", embeds=embeds)


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
    await interaction.followup.send(
        content=f"**Transactions — {team.title()}**", embeds=embeds
    )


@bot.tree.command(name="news", description="Get the latest NFL news headlines")
async def cmd_news(interaction: discord.Interaction):
    await interaction.response.defer()
    items = get_news(limit=5)
    if not items:
        await interaction.followup.send("⚠️ No news found. Try again shortly.")
        return
    await interaction.followup.send(embed=news_embed(items))


@bot.tree.command(name="source", description="Set the auto-post news source")
@app_commands.describe(source="News source to use for auto-posting")
@app_commands.choices(source=[
    app_commands.Choice(name="ESPN transactions", value="espn"),
    app_commands.Choice(name="Bluesky beat writers", value="bluesky"),
    app_commands.Choice(name="Both", value="both"),
])
async def cmd_source(interaction: discord.Interaction, source: str):
    settings = load_settings()
    settings["source"] = source
    save_settings(settings)
    labels = {"espn": "ESPN", "bluesky": "Bluesky", "both": "Both (ESPN + Bluesky)"}
    await interaction.response.send_message(
        f"✅ Auto-post source set to **{labels[source]}**.", ephemeral=True
    )


@bot.tree.command(name="writers", description="View or toggle Bluesky beat writers")
@app_commands.describe(writer="Writer to toggle on/off (omit to view all)")
@app_commands.choices(writer=_WRITER_CHOICES)
async def cmd_writers(interaction: discord.Interaction, writer: str | None = None):
    settings = load_settings()
    disabled = set(settings.get("disabled_writers", []))

    # No argument — show status of all writers
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
        embed.set_footer(text="Use /writers <name> to toggle a writer on or off")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Toggle the selected writer
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


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN is not set in your .env file")
    bot.run(TOKEN)
