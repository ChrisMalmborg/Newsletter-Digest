import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

# Anthropic API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# IMAP Configuration
IMAP_HOST = os.getenv("IMAP_HOST")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USERNAME = os.getenv("IMAP_USERNAME")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")

# SMTP Configuration
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Digest settings
DIGEST_TO_ADDRESS = os.getenv("DIGEST_TO_ADDRESS")

# Database
DATABASE_PATH = DATA_DIR / "newsletters.db"
DATABASE_URL = os.getenv("DATABASE_URL")  # Set in production for PostgreSQL

# Google OAuth
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")

# Web app
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "change-me-in-production")

# Cron / scheduled jobs
CRON_SECRET = os.getenv("CRON_SECRET", "")

# Admin dashboard (email of the admin user)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
