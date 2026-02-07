#!/usr/bin/env python3
"""Trigger the daily digest via the web API.

Designed to be called by Railway cron (or any scheduler).  Makes an HTTP POST
to the /api/run-digest endpoint with the CRON_SECRET for authentication.

Usage:
    python scripts/trigger_daily.py
    python scripts/trigger_daily.py --base-url https://my-app.up.railway.app

Environment variables:
    CRON_SECRET   — shared secret that the API checks
    BASE_URL      — base URL of the running web app (default: http://localhost:8000)
"""
import logging
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    cron_secret = os.getenv("CRON_SECRET", "")
    if not cron_secret:
        logger.error("CRON_SECRET environment variable is not set")
        sys.exit(1)

    # Allow override via CLI arg or env var
    if len(sys.argv) > 2 and sys.argv[1] == "--base-url":
        base_url = sys.argv[2]
    else:
        base_url = os.getenv("BASE_URL", "http://localhost:8000")

    url = "{}/api/run-digest".format(base_url.rstrip("/"))
    logger.info("Triggering digest run at %s", url)

    req = Request(url, method="POST", data=b"")
    req.add_header("X-Cron-Secret", cron_secret)
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=600) as resp:
            body = resp.read().decode()
            logger.info("Response (%d): %s", resp.status, body)
    except HTTPError as e:
        body = e.read().decode()
        logger.error("HTTP %d: %s", e.code, body)
        sys.exit(1)
    except URLError as e:
        logger.error("Request failed: %s", e.reason)
        sys.exit(1)


if __name__ == "__main__":
    main()
