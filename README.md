# TLDRead

An AI-powered tool that aggregates newsletters from your Gmail inbox, summarizes them using Claude, identifies themes across multiple newsletters, and delivers a daily digest email.

## Features

- **Gmail OAuth integration** — reads from your existing inbox, no forwarding needed
- **AI-powered summarization** — Claude distills each newsletter into key takeaways
- **Cross-newsletter theme detection** — finds related stories across different sources
- **Daily automated digest delivery** — a single email with everything you need to know
- **Web dashboard** — view past digests and manage subscriptions
- **Smart newsletter detection** — distinguishes newsletters from transactional emails

## Tech Stack

| Component | Technology |
|-----------|------------|
| **Backend** | Python, FastAPI |
| **AI** | Anthropic Claude API (claude-sonnet-4-5-20250929) |
| **Auth** | Google OAuth 2.0, Gmail API |
| **Database** | SQLite |
| **Deployment** | Railway |
| **Scheduling** | Cron-job.org |

## Screenshots

> *[Screenshot of digest email]*

> *[Screenshot of web dashboard]*

## How It Works

1. **Connect** your Gmail account via OAuth
2. **Select** which newsletters to include
3. **Every day at 11am**, the system fetches new newsletters from your inbox
4. **Claude summarizes** each one, finds cross-newsletter themes, and emails you the digest

## Local Development

### Prerequisites

- Python 3.11+
- A [Google Cloud project](https://console.cloud.google.com/) with the Gmail API enabled and OAuth 2.0 credentials configured
- An [Anthropic API key](https://console.anthropic.com/)

### Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/newsletter-digest.git
cd newsletter-digest

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy the example env file and fill in your credentials
cp .env.example .env

# Initialize the database
python scripts/setup_db.py

# Start the development server
python3 -m uvicorn src.web.app:app --port 8000 --reload
```

Then visit `http://localhost:8000` to connect your Gmail account and get started.

### Environment Variables

```
ANTHROPIC_API_KEY=           # Anthropic API key
GOOGLE_CLIENT_ID=            # Google OAuth client ID
GOOGLE_CLIENT_SECRET=        # Google OAuth client secret
GOOGLE_REDIRECT_URI=         # http://localhost:8000/auth/callback (for local dev)
SESSION_SECRET_KEY=          # Random string for session signing
CRON_SECRET=                 # Secret for authenticating scheduled digest runs
```

### Running a Digest Manually

```bash
# Full digest run
python scripts/run_daily.py --user your@gmail.com

# Dry run (generates digest without sending)
python scripts/run_daily.py --user your@gmail.com --dry-run

# Look back 48 hours instead of 24
python scripts/run_daily.py --user your@gmail.com --hours 48
```

## Architecture

```
src/
├── ingestion/     # Gmail API client, email parsing
├── processing/    # Claude summarization, theme clustering
├── delivery/      # Digest building, email sending
└── web/           # FastAPI app, OAuth flow, dashboard
```

- **`src/ingestion/`** — Connects to Gmail via OAuth, fetches emails, and parses HTML content into clean text
- **`src/processing/`** — Sends newsletter content to Claude for summarization, then clusters summaries to identify cross-newsletter themes
- **`src/delivery/`** — Assembles the final digest from summaries and themes, renders HTML templates, and sends via Gmail API
- **`src/web/`** — FastAPI application handling OAuth login, newsletter selection, digest history, and the scheduled digest API endpoint

## License

MIT
