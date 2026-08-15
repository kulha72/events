"""
Adrian / Lenawee County local events collector.
Scrapes visitlenawee.com events calendar.

The scraper no longer assumes The Events Calendar's markup. It walks the
layers in collectors/local/eventpage.py — REST API, JSON-LD, microdata, then
CSS selectors, re-trying the page-level layers against a rendered DOM — so a
re-theme or a switch to client-side rendering downgrades the scrape instead of
emptying it.
"""

import uuid
from datetime import date, timezone
from zoneinfo import ZoneInfo

import requests

from collectors.base import BaseCollector
from collectors.local import eventpage
from models.event import Event, EventCategory

LOCAL_TZ = ZoneInfo("America/Detroit")
BASE_URL = "https://www.visitlenawee.com/events/"
DEFAULT_LOCATION = "Adrian, MI"

# Selectors the calendar is known to have used, plus the generic shapes from
# eventpage. Tried only after the structured layers come up empty.
ITEM_SELECTORS = (
    "article.type-tribe_events",
    ".tribe-events-loop .tribe-events-calendar-list__event",
    ".tribe-event-list-item",
    ".tribe-events-loop article",
) + eventpage.DEFAULT_ITEM_SELECTORS

# What to wait for when rendering: any of these means the calendar has drawn.
WAIT_SELECTORS = (
    "article.type-tribe_events",
    ".tribe-events-calendar-list__event",
    "[class*='event-item']",
    "script[type='application/ld+json']",
)

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; daily-digest-adrian/1.0)"})


def _scrape_events(today: date, lookahead_days: int) -> list[dict]:
    return eventpage.scrape_calendar(
        source="adrian",
        session=_session,
        page_url=BASE_URL,
        today=today,
        lookahead_days=lookahead_days,
        tz=LOCAL_TZ,
        default_location=DEFAULT_LOCATION,
        site_label="visitlenawee.com",
        item_selectors=ITEM_SELECTORS,
        wait_selectors=WAIT_SELECTORS,
    )


class AdrianCollector(BaseCollector):

    def __init__(self, config: dict):
        self.config = config

    @property
    def source_name(self) -> str:
        return "adrian"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        import cache

        cached = cache.get("adrian", ttl_seconds=3600 * 24)
        if cached:
            return [Event(**e) for e in cached]

        raw_events = _scrape_events(today, lookahead_days)
        events = []

        for raw in raw_events:
            start_utc = raw["start_dt"].astimezone(timezone.utc)
            end_utc = raw["end_dt"].astimezone(timezone.utc) if raw.get("end_dt") else None

            events.append(Event(
                id=f"adrian:{uuid.uuid5(uuid.NAMESPACE_URL, raw['url'] + str(raw['start_dt']))}",
                title=raw["title"],
                category=EventCategory.LOCAL,
                start=start_utc,
                end=end_utc,
                location=raw.get("location") or DEFAULT_LOCATION,
                source="adrian",
                url=raw.get("url") or BASE_URL,
                tags=["local", "adrian", "lenawee"],
            ))

        return eventpage.within_window(events, today, lookahead_days, LOCAL_TZ, "adrian")
