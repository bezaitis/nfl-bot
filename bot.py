"""
bot.py — NFL Discord Bot
--------------------------
Slash commands:
  /transactions        — latest NFL transactions
  /team <name>         — transactions filtered by team
  /news                — latest NFL headlines

Auto-posting:
  Every 30 minutes the bot checks for new transactions and posts them
  to the configured NEWS_CHANNEL_ID, deduplicating against seen_ids.json.

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

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("NEWS_CHANNEL_ID", "0"))
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
SEEN_FILE = "seen_ids.json"

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


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


# ── Embed builders ────────────────────────────────────────────────────────────
def transaction_embed(item: dict, reason: str = "") -> discord.Embed:
    team = item.get("team") or "NFL"
    embed = discord.Embed(
        description=item["description"],
        color=discord.Color.from_str("#013369"),  # NFL navy
        timestamp=datetime.now(timezone.utc),
    )
    author_line = f"🏈 {team}" + (f"  ·  {reason}" if reason else "")
    embed.set_author(name=author_line)
    if item.get("date"):
        embed.set_footer(text=item["date"][:10])
    return embed


def news_embed(items: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title="📰 Latest NFL News",
        color=discord.Color.from_str("#D50A0A"),  # NFL red
        timestamp=datetime.now(timezone.utc),
    )
    for item in items:
        # Truncate summary for field value limit
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
        auto_post.start()
        print(f"⏱  Auto-posting to channel {CHANNEL_ID} every {CHECK_INTERVAL_MINUTES} min")
    else:
        print("⚠️  NEWS_CHANNEL_ID not set — auto-posting disabled")


# ── Scheduled task ────────────────────────────────────────────────────────────
@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def auto_post():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"[auto_post] Channel {CHANNEL_ID} not found")
        return

    seen = load_seen()
    all_transactions = get_all_transactions(limit=50)

    # Filter to only notable trades, skipping already-seen items
    notable = []
    for t in all_transactions:
        if t["id"] in seen:
            continue
        should_post, reason = is_notable_transaction(t)
        if should_post:
            notable.append((t, reason))

    if not notable:
        print("[auto_post] No new notable trades found")
        return

    # Post up to 5 per cycle to avoid spam
    for item, reason in notable[:5]:
        try:
            await channel.send(embed=transaction_embed(item, reason))
            seen.add(item["id"])
        except discord.HTTPException as e:
            print(f"[auto_post] Failed to send message: {e}")

    # Mark ALL seen transactions (not just notable) to avoid re-evaluating them
    for t in all_transactions:
        seen.add(t["id"])

    save_seen(seen)
    print(f"[auto_post] Posted {min(len(notable), 5)} notable trade(s)")


@auto_post.before_loop
async def before_auto_post():
    await bot.wait_until_ready()


# ── Slash commands ─────────────────────────────────────────────────────────────
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


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN is not set in your .env file")
    bot.run(TOKEN)
