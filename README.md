# 🏈 NFL Discord Bot

A Discord bot that tracks NFL transactions and news, posting them to your server automatically and on-demand via slash commands.

**Data source:** ESPN's public APIs and RSS feed — no API key required.

---

## Features

| Feature | Details |
|---|---|
| `/transactions` | Latest NFL trades & roster moves |
| `/team <name>` | Transactions filtered by team (e.g. `/team Bears`, `/team CHI`) |
| `/news` | Latest NFL headlines from ESPN |
| Auto-posting | Posts new transactions to a channel every 30 min (configurable) |
| Deduplication | Tracks seen items so nothing gets posted twice |

---

## Setup

### 1. Create a Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** → give it a name
3. Go to **Bot** → click **Add Bot**
4. Under **Token**, click **Reset Token** and copy it — this is your `DISCORD_TOKEN`
5. Under **Privileged Gateway Intents**, enable **Message Content Intent** (optional but safe to enable)
6. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Read Message History`
7. Copy the generated URL, open it in your browser, and invite the bot to your server

### 2. Get Your Channel ID

1. In Discord, go to **User Settings → Advanced** and enable **Developer Mode**
2. Right-click the channel you want the bot to post in → **Copy Channel ID**

### 3. Install & Configure

```bash
# Clone or download this project, then:
cd nfl-bot

# Install dependencies
pip install -r requirements.txt

# Set up your environment
cp .env.example .env
# Edit .env and fill in DISCORD_TOKEN and NEWS_CHANNEL_ID
```

### 4. Run

```bash
python bot.py
```

You should see:
```
✅ Logged in as YourBot#1234 (ID: ...)
⚡ Synced 3 slash command(s)
⏱  Auto-posting to channel ... every 30 min
```

Slash commands may take up to 1 hour to appear globally, but will work immediately in your server.

---

## Configuration

Edit `.env` to change behavior:

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | required | Your bot token |
| `NEWS_CHANNEL_ID` | required | Channel for auto-posts |
| `CHECK_INTERVAL_MINUTES` | `30` | How often to check for new transactions |

---

## Project Structure

```
nfl-bot/
├── bot.py              # Discord bot, slash commands, scheduled task
├── fetcher.py          # ESPN API + RSS data fetching
├── requirements.txt
├── .env.example        # Rename to .env and fill in
├── seen_ids.json       # Auto-created; tracks posted transactions
└── README.md
```

---

## Extending It

Some ideas for future additions:
- **Injury reports** — ESPN has a public injuries endpoint: `site.api.espn.com/apis/site/v2/sports/football/nfl/injuries`
- **Game scores** — `/scores` command using the ESPN scoreboard API
- **Fantasy alerts** — Snap count and target share changes scraped from FantasyPros or PFF
- **Daily digest** — A `/digest` command that summarizes all moves from the past 24 hours
- **Persistent storage** — Swap `seen_ids.json` for SQLite with `aiosqlite` for more robust deduplication

---

## Tech Stack

- [discord.py](https://discordpy.readthedocs.io/) — Discord bot framework
- [feedparser](https://feedparser.readthedocs.io/) — RSS parsing
- [requests](https://requests.readthedocs.io/) — HTTP requests
- [python-dotenv](https://pypi.org/project/python-dotenv/) — Environment variable management
