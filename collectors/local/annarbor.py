"""
Ann Arbor local events collector.
Scrapes visitannarbor.org events calendar.

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
BASE_URL = "https://www.visitannarbor.org/events/"
DEFAULT_LOCATION = "Ann Arbor, MI"

# Selectors the calendar is known to have used, plus the generic shapes from
# eventpage. Tried only after the structured layers come up empty.
ITEM_SELECTORS = (
    ".tribe-events-calendar article",
    ".tribe-event-list-item",
    ".wp-block-tribe-event-list .tribe-event",
    "article.type-tribe_events",
) + eventpage.DEFAULT_ITEM_SELECTORS

# What to wait for when rendering: any of these means the calendar has drawn.
WAIT_SELECTORS = (
    ".tribe-events-calendar article",
    "article.type-tribe_events",
    "[class*='event-item']",
    "script[type='application/ld+json']",
)

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; daily-digest-annarbor/1.0)"})


def _scrape_events(today: date, lookahead_days: int) -> list[dict]:
    return eventpage.scrape_calendar(
        source="annarbor",
        session=_session,
        page_url=BASE_URL,
        today=today,
        lookahead_days=lookahead_days,
        tz=LOCAL_TZ,
        default_location=DEFAULT_LOCATION,
        site_label="visitannarbor.org",
        item_selectors=ITEM_SELECTORS,
        wait_selectors=WAIT_SELECTORS,
    )


class AnnArborCollector(BaseCollector):

    def __init__(self, config: dict):
        self.config = config

    @property
    def source_name(self) -> str:
        return "annarbor"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        import cache

        cached = cache.get("annarbor", ttl_seconds=3600 * 12)
        if cached:
            return [Event(**e) for e in cached]

        raw_events = _scrape_events(today, lookahead_days)
        events = []

        for raw in raw_events:
            start_utc = raw["start_dt"].astimezone(timezone.utc)
            end_utc = raw["end_dt"].astimezone(timezone.utc) if raw.get("end_dt") else None

            events.append(Event(
                id=f"annarbor:{uuid.uuid5(uuid.NAMESPACE_URL, raw['url'] + str(raw['start_dt']))}",
                title=raw["title"],
                category=EventCategory.LOCAL,
                start=start_utc,
                end=end_utc,
                location=raw.get("location") or DEFAULT_LOCATION,
                source="annarbor",
                url=raw.get("url") or BASE_URL,
                tags=["local", "ann-arbor"],
            ))

        return eventpage.within_window(events, today, lookahead_days, LOCAL_TZ, "annarbor")
