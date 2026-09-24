#!/usr/bin/env python3
"""
Tests for the per-town local event toggles on the static page.

Each local event card carries a data-area attribute that the page's Preferences
panel filters on. A local collector whose `source` is missing from LOCAL_AREAS
would render cards with no data-area, and those could never be switched off —
so these tests pin every local source to a town.

Run: python test_local_areas.py
"""

import glob
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

import formatters.static_formatter as static_formatter
from formatters.static_formatter import LOCAL_AREAS, local_area
from models.event import Event, EventCategory

_failures: list[str] = []

_HERE = os.path.dirname(os.path.abspath(__file__))


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {label}")
    else:
        print(f"  FAIL {label}{': ' + detail if detail else ''}")
        _failures.append(label)


def _event(source: str, category: EventCategory = EventCategory.LOCAL) -> Event:
    return Event(
        id=f"test:{source}",
        title=f"{source} event",
        category=category,
        start=datetime(2026, 9, 24, 20, tzinfo=timezone.utc),
        source=source,
    )


def test_every_local_collector_source_has_an_area():
    """Every source= a local collector writes for a LOCAL event maps to a town."""
    print("\n[test_every_local_collector_source_has_an_area]")
    for path in sorted(glob.glob(os.path.join(_HERE, "collectors", "local", "*.py"))):
        text = open(path, encoding="utf-8").read()
        if "EventCategory.LOCAL" not in text:
            continue
        for source in re.findall(r'source="([^"]+)"', text):
            check(f"{os.path.basename(path)}: {source}", source in LOCAL_AREAS,
                  "add it to LOCAL_AREAS in formatters/static_formatter.py")


def test_only_local_events_get_an_area():
    print("\n[test_only_local_events_get_an_area]")
    check("ann arbor", local_area(_event("annarbor")) == "annarbor")
    check("both tecumseh calendars share a toggle",
          local_area(_event("downtown_tecumseh")) == local_area(_event("tecumseh_herald")) == "tecumseh")
    check("estate sales are not filtered by town",
          local_area(_event("estatesales", EventCategory.ESTATE_SALES)) == "")
    check("sports are not filtered by town",
          local_area(_event("espn", EventCategory.SPORTS)) == "")


def test_rendered_page_has_town_chips_and_tagged_cards():
    print("\n[test_rendered_page_has_town_chips_and_tagged_cards]")
    static_formatter._fetch_npr_headlines = lambda n=5: []
    empty_health = {k: [] for k in
                    ("failures", "suspect", "empty_sources", "degraded", "idle", "not_configured")}
    with tempfile.TemporaryDirectory() as out:
        static_formatter.OUTPUT_DIR = out
        html = static_formatter.format_static_page(
            [_event("adrian"), _event("espn", EventCategory.SPORTS)], [], [],
            {"lookahead_days": 7, "timezone": "America/Detroit"},
            health=empty_health,
        )
    chips = re.findall(r'class="pref-chip" data-area="([^"]+)"', html)
    check("one chip per town, in order", chips == ["annarbor", "adrian", "tecumseh", "tca"], str(chips))
    check("adrian card is tagged", 'data-cat="local" data-area="adrian"' in html)
    check("sports card is not tagged", 'data-cat="sports" data-area' not in html)


if __name__ == "__main__":
    test_every_local_collector_source_has_an_area()
    test_only_local_events_get_an_area()
    test_rendered_page_has_town_chips_and_tagged_cards()
    print()
    if _failures:
        print(f"{len(_failures)} failure(s): {', '.join(_failures)}")
        sys.exit(1)
    print("All tests passed.")
