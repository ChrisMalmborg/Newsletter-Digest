#!/usr/bin/env python3
"""Initialize the database schema."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import init_db
from src.config import DATABASE_PATH


def main():
    print(f"Initializing database at {DATABASE_PATH}...")
    init_db()
    print("Database initialized successfully!")


if __name__ == "__main__":
    main()
