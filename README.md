# Jarvis — Telegram AI Assistant

A personal AI assistant powered by Claude, accessible through Telegram. Manages your calendar, email, tasks, reminders, notes, expenses, and more.

## Features

- **Google Calendar** — Create, view, update, delete events via natural language
- **Gmail** — Search, read, draft replies to emails
- **Task Management** — Full CRUD with priorities (low/medium/high/urgent) and statuses
- **Reminders** — Time-based reminders delivered via Telegram (supports recurring)
- **Notes & Knowledge Base** — Save, search, and retrieve personal notes
- **Expense Tracking** — Log expenses, categorize, get spending summaries
- **Daily Briefing** — Auto morning briefing + on-demand (calendar, tasks, emails, reminders)
- **Voice Messages** — Transcribes Telegram voice notes and processes as commands
- **Web Research** — Search the web and get summarized results

## Tech Stack

- **Python 3.11+** / FastAPI / Uvicorn
- **Claude API** (Anthropic) — AI brain with tool use
- **Telegram Bot API**
- **Supabase** — PostgreSQL database
- **Google APIs** — Calendar + Gmail via OAuth2
- **OpenAI Whisper** — Voice transcription
- **APScheduler** — Background jobs (reminders, daily briefing)

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Telegram bot (create via [@BotFather](https://t.me/BotFather))
- A [Supabase](https://supabase.com/) project (free tier works)
- An [Anthropic API key](https://console.anthropic.com/)
- A [Google Cloud Console](https://console.cloud.google.com/) project with Calendar & Gmail APIs enabled
- (Optional) An [OpenAI API key](https://platform.openai.com/) for voice transcription

### 2. Create Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** you receive
4. To get your **chat ID**, message [@userinfobot](https://t.me/userinfobot) and copy the number

### 3. Setup

```bash
cd "AI assistant"
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your credentials
```

### 4. Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable Calendar API + Gmail API
3. Create OAuth2 credentials (Web application)
4. Add redirect URI: `https://your-domain.com/auth/google/callback`
5. Copy Client ID and Client Secret to `.env`

### 5. Run

```bash
# Start the server
uvicorn app.main:app --reload

# Connect Google account (one-time)
python scripts/setup_google_oauth.py
```

The server will automatically register the Telegram webhook when it starts (if `APP_BASE_URL` is set to an `https://` URL).

### 6. Deploy to Railway

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Set all environment variables in the Railway dashboard.

## Usage Examples

| Message | What happens |
|---------|-------------|
| "What's on my calendar today?" | Shows today's events |
| "Schedule a meeting with John tomorrow at 3pm" | Creates calendar event |
| "Add task: finish report by Friday, high priority" | Creates a task |
| "Remind me to call mom at 5pm" | Sets a reminder |
| "Spent $15 on coffee" | Logs an expense |
| "How much did I spend this month?" | Shows expense summary |
| "Save note: Q2 budget is $50k" | Saves a note |
| "Search notes about budget" | Finds matching notes |
| "Give me my briefing" | Daily summary |
| "Research latest AI trends" | Web search + summary |
| *Send a voice note* | Transcribes and processes |

## Architecture

```
Telegram → Bot API → POST /webhook → FastAPI
  → Claude API (with 20+ tool definitions)
  → Tool-use loop (up to 5 iterations)
  → Service execution (Calendar, Gmail, Tasks, etc.)
  → Response formatted for Telegram
  → Send via Bot API → Telegram
```

## License

MIT
