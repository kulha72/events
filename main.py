#!/usr/bin/env python3
"""
Daily Digest — main pipeline entry point.

Flow: collect -> normalize/flag -> format -> deliver

Usage:
    python main.py                   # Full run
    python main.py --dry-run         # Collect + format, skip delivery
    python main.py --no-email        # Skip Gmail send
    python main.py --no-deploy       # Skip GitHub Pages push
"""

import argparse
import sys
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

# Ensure the daily-digest directory is on the path so imports work
# whether run as `python main.py` from inside daily-digest/ or from the project root.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

load_dotenv(os.path.join(_HERE, ".env"))

from collectors.local.tecumseh import TecumsehCollector
from collectors.local.annarbor import AnnArborCollector
from collectors.local.adrian import AdrianCollector
from collectors.local.tca import TCACollector
from collectors.local.estatesales import EstateSalesCollector
from collectors.sports.espn import ESPNCollector
from collectors.sports.football_data import FootballDataCollector
from collectors.sports.api_football import APIFootballCollector
from collectors.esports.liquipedia import LiquipediaCollector
from collectors.esports.pandascore import PandaScoreCollector
from collectors.esports.startgg import StartGGCollector
from formatters.email_formatter import format_email
from formatters.static_formatter import format_static_page
from formatters.telegram_formatter import format_telegram
from delivery.gmail import send_email
from delivery.ghpages import deploy_page
from delivery.telegram import send_telegram
from models.event import Event, EventPriority


def load_config(path: str = "config.yaml") -> dict:
    config_path = os.path.join(_HERE, path)
    with open(config_path) as f:
        return yaml.safe_load(f)


def compute_flags(events: list[Event], today: date, tz: ZoneInfo) -> None:
    """Set is_today and is_past on each event based on local date.

    Multi-day events (e.g. estate sales) that started before today but haven't
    ended yet are treated as today rather than past.
    """
    for event in events:
        start_date = event.start.astimezone(tz).date()
        end_date = event.end.astimezone(tz).date() if event.end else start_date
        if start_date == today or (start_date < today and end_date >= today):
            event.is_today = True
            event.is_past = False
        elif end_date < today:
            event.is_today = False
            event.is_past = True
        else:
            event.is_today = False
            event.is_past = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Digest pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Skip all delivery steps")
    parser.add_argument("--no-email", action="store_true", help="Skip Gmail send")
    parser.add_argument("--no-telegram", action="store_true", help="Skip Telegram send")
    parser.add_argument("--no-deploy", action="store_true", help="Skip GitHub Pages deploy")
    args = parser.parse_args()

    config = load_config()
    today = date.today()
    lookahead = config.get("lookahead_days", 7)
    tz = ZoneInfo(config.get("timezone", "America/Detroit"))

    print(f"Daily Digest — {today.strftime('%A, %B %-d, %Y')}")
    print(f"Lookahead: {lookahead} days | Timezone: {config.get('timezone')}")
    print()

    # 1. Collect from all sources
    collectors = [
        TecumsehCollector(config),
        AnnArborCollector(config),
        AdrianCollector(config),
        TCACollector(config),
        EstateSalesCollector(config),
        ESPNCollector(config),
        FootballDataCollector(config),
        APIFootballCollector(config),
        LiquipediaCollector(config),
        PandaScoreCollector(config),
        StartGGCollector(config),
    ]

    # Filter to only enabled sources
    local_enabled = {s["name"] for s in config.get("local", {}).get("sources", []) if s.get("enabled", True)}

    all_events: list[Event] = []
    for collector in collectors:
        # Skip disabled local sources
        if collector.source_name in ("tecumseh", "annarbor", "adrian", "tca", "estatesales"):
            if collector.source_name not in local_enabled:
                continue

        print(f"Collecting: {collector.source_name}...")
        try:
            events = collector.collect(today, lookahead)
            print(f"  -> {len(events)} events")
            all_events.extend(events)
        except Exception as e:
            print(f"  Warning: {collector.source_name} failed: {e}")
            # Continue — one failed source should not break the whole digest

    print(f"\nTotal collected: {len(all_events)} events")

    # 2. Compute flags and sort
    compute_flags(all_events, today, tz)
    all_events.sort(key=lambda e: (e.start, e.priority.value))

    # 3. Partition
    today_events = [e for e in all_events if e.is_today]
    yesterday_results = [e for e in all_events if e.is_past and e.result]
    upcoming = [e for e in all_events if not e.is_today and not e.is_past]

    print(f"Today: {len(today_events)} | Results: {len(yesterday_results)} | Upcoming: {len(upcoming)}")
    print()

    # 4. Format
    print("Formatting email...")
    email_html = format_email(today_events, yesterday_results, upcoming, config)

    print("Formatting static page...")
    page_html = format_static_page(today_events, yesterday_results, upcoming, config)

    # 5. Deliver
    if args.dry_run:
        print("\nDry run — skipping delivery.")
        return

    if not args.no_email:
        print("\nSending email...")
        send_email(email_html, config)

    if not args.no_telegram:
        print("\nSending Telegram message...")
        tg_messages = format_telegram(today_events, yesterday_results, upcoming, config)
        send_telegram(tg_messages)

    if not args.no_deploy:
        print("\nDeploying to GitHub Pages...")
        deploy_page(page_html, config)

    print("\nDone.")


if __name__ == "__main__":
    main()
