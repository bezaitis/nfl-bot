# 🏈 NFL Discord Bot

A Discord bot that tracks NFL transactions and news, posting them to your server automatically and on-demand via slash commands.

**Data sources:** ESPN public APIs/RSS (transactions & headlines) + Bluesky beat writers (breaking news) — no API keys required.

---

## Features

| Feature | Details |
|---|---|
| `/team <name>` | Transactions filtered by team (e.g. `/team Bears`, `/team CHI`) |
| `/news [team]` | Latest NFL headlines from ESPN, optionally team-filtered |
| `/player <name>` | NFL player profile — position, team, size, headshot, ESPN link |
| `/source <espn\|bluesky\|both>` | Set which source the auto-post loop uses (requires Manage Server) |
| `/interval <minutes>` | Set the ESPN auto-post check frequency: 10/30/60/120 min (requires Manage Server) |
| `/writers [writer]` | View all Bluesky beat writers and toggle them on/off |
| `/settings` | Interactive dashboard with dropdowns for source, interval, and writers (requires Manage Server) |
| `/help` | Show all commands and usage |
| ESPN auto-posting | Posts top-scored news stories every 30 min (configurable) |
| Bluesky auto-posting | Posts beat writer updates every 10 min |
| Weighted scoring | Each signal adds points; items above a threshold post, highest first (see below) |
| Team-branded embeds | Embeds use the team's color and logo, with link buttons to the source |
| Deduplication | Tracks seen items — nothing gets posted twice |
| Story deduplication | Only the first writer to break a story is posted |

---

## Post scoring

Both pipelines run candidate text through the weighted scorer in `scoring.py`.
Each signal adds (or subtracts) points; anything scoring at or above the
threshold (4) gets posted, highest score first, capped at 5 per cycle —
overflow stays queued for the next cycle.

| Signal | Points |
|---|---|
| Chicago Bears mention (the followed team) | +4 |
| Star player name (curated list, ~150 names) | +3 |
| Contract AAV ≥ $20M / ≥ $10M | +3 / +2 |
| Total ≥ $100M (years unparseable) | +2 |
| Draft pick mention | +2 |
| Strong transaction keyword (signed, traded, waived, …) | +2 |
| Sourcing/financial corroboration ("per sources", dollar figures, …) | +1 |
| Opinion/hypothetical phrasing ("should have traded …") | −4 |

In practice: a Bears mention alone posts; a star player needs a transaction
keyword; draft-pick or money language needs a strong keyword; opinion pieces
don't post even with strong keywords. Weights are constants at the top of
`scoring.py` — tune them there and update `test_scoring.py` to match.

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

### 4. First Run (register slash commands)

```bash
SYNC_COMMANDS=1 python bot.py
```

Slash commands may take up to 1 hour to appear globally, but will work immediately in your server.

### 5. Subsequent Runs

```bash
python bot.py
```

---

## Configuration

Edit `.env` to change behavior:

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | required | Your bot token |
| `NEWS_CHANNEL_ID` | required | Channel for auto-posts |
| `CHECK_INTERVAL_MINUTES` | `30` | How often to check ESPN for new transactions |
| `SYNC_COMMANDS` | `0` | Set to `1` on first run to register slash commands |

Runtime settings (source, disabled writers) are stored in `settings.json` and persist across restarts.

---

## Project Structure

```
nfl-bot/
├── bot.py              # Discord bot, slash commands, scheduled tasks, /settings panel
├── fetcher.py          # ESPN API + RSS data fetching
├── scoring.py          # Weighted post scorer (team, stars, money, draft picks, …)
├── storage.py          # JSON persistence for seen IDs and settings
├── teams.py            # Team branding (embed colors + logos) from ESPN
├── title_parser.py     # Structured title extraction from raw ESPN descriptions
├── bluesky.py          # Bluesky beat writer fetcher with story deduplication
├── test_dedup.py       # Seen-ID persistence tests
├── test_scoring.py     # Scorer calibration tests
├── requirements.txt
├── .env.example        # Rename to .env and fill in
├── seen_ids.json       # Auto-created; tracks posted item IDs
├── settings.json       # Auto-created; stores source, interval, writer toggle state
└── README.md
```

---

## Beat Writers (Bluesky)

The bot follows these NFL beat writers on Bluesky and posts their NFL-related updates every 10 minutes:

- Ian Rapoport (NFL Network)
- Dianna Russini (The Athletic)
- Tom Pelissero (NFL Network)
- Steve Wyche (NFL Network)
- Kevin Seifert (ESPN)
- Alaina Getzenberg (ESPN)
- Marcel Louis-Jacques (ESPN)
- Jamison Hensley (ESPN)
- Jenna Laine (ESPN)
- Ted Nguyen (The Athletic)
- Mike Tanier (Freelance)
- ProFootballTalk (NBC Sports)

Use `/writers` in Discord to toggle any writer on or off, `/source` to switch between ESPN only, Bluesky only, or both — or `/settings` to manage everything from one dashboard.

---

## Extending It

Some ideas for future additions:
- **Add beat writers** — `/writers add <handle>` command to add new Bluesky accounts at runtime
- **Injury reports** — ESPN has a public injuries endpoint: `site.api.espn.com/apis/site/v2/sports/football/nfl/injuries`
- **Game scores** — `/scores` command using the ESPN scoreboard API
- **Daily digest** — A `/digest` command that summarizes all moves from the past 24 hours
- **Persistent storage** — Swap `seen_ids.json` for SQLite with `aiosqlite` for more robust deduplication

---

## Tech Stack

- [discord.py](https://discordpy.readthedocs.io/) — Discord bot framework
- [feedparser](https://feedparser.readthedocs.io/) — RSS parsing
- [requests](https://requests.readthedocs.io/) — HTTP requests
- [python-dotenv](https://pypi.org/project/python-dotenv/) — Environment variable management
