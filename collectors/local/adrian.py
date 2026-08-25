"""
Adrian / Lenawee County local events collector.

visitlenawee.com no longer runs its own calendar. The events page is a shell
around a Yodel widget in an iframe (events.yodel.today), so the old scraper
was parsing the frame around the calendar rather than the calendar — every
layer it tried, REST through CSS selectors, was looking at the wrong document.

This follows the embed instead:

  1. Read the widget id out of the bureau's own page, so a re-embed is picked
     up rather than hard-coded
  2. Render the widget and read its schema.org JSON-LD, which is what Yodel
     publishes for search engines

The widget answers a plain HTTP request with 403, so the render is not
optional here.

Requires: playwright, with chromium installed.
"""

import re
import uuid
from datetime import date, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from collectors.base import BaseCollector
from collectors.local import eventpage
from models.event import Event, EventCategory

import errors

LOCAL_TZ = ZoneInfo("America/Detroit")
BASE_URL = "https://www.visitlenawee.com/events/"
DEFAULT_LOCATION = "Adrian, MI"

# The embed the bureau was using when this was written; only a fallback, since
# _discover_widget_id reads the current one off the page first.
KNOWN_WIDGET_ID = "699331672d0ab3b826bf79e5"
_WIDGET_ID_RE = re.compile(r"events\.yodel\.today/y/widget/([0-9a-f]{16,32})", re.I)

# What to wait for: any of these means the widget has drawn its cards.
WAIT_SELECTORS = (
    "[class*='eventcardtile']",
    "[id='eventContainer']",
    "script[type='application/ld+json']",
)

_session = requests.Session()
_session.headers.update({
    "User-Agent": eventpage.BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})


def _widget_url(widget_id: str) -> str:
    return f"https://events.yodel.today/y/widget/{widget_id}"


def _discover_widget_id() -> tuple[str | None, str]:
    """Read the Yodel embed id off visitlenawee.com's events page.

    Returns the id and, when there isn't one, why — which only becomes worth
    reporting if the fallback id fails too.
    """
    try:
        resp = _session.get(BASE_URL, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        return None, f"could not fetch {BASE_URL}: {e}"

    found = _WIDGET_ID_RE.search(resp.text)
    if found:
        return found.group(1), ""

    return None, (
        f"visitlenawee.com: no Yodel widget embedded on the events page any more "
        f"— {eventpage.describe_page(BeautifulSoup(resp.text, 'html.parser'))}"
    )


def _scrape_events() -> list[dict]:
    widget_id, why_not = _discover_widget_id()
    from_page = bool(widget_id)
    widget_id = widget_id or KNOWN_WIDGET_ID

    url = _widget_url(widget_id)
    try:
        soup = eventpage.render_page(url, wait_selectors=WAIT_SELECTORS, timeout_ms=45_000)
    except Exception as e:
        if why_not:
            errors.record("adrian", why_not)
        errors.record("adrian", f"could not render the Lenawee events widget: {e}")
        return []

    events, how = eventpage.extract_all(
        soup, LOCAL_TZ, DEFAULT_LOCATION, eventpage.DEFAULT_ITEM_SELECTORS, url
    )
    if events:
        errors.note_strategy(
            "adrian",
            f"Yodel widget: {len(events)} events from {how}"
            + ("" if from_page else " — using the hard-coded widget id"),
            # Reading the third-party markup, or guessing which widget, are
            # both a step short of solid.
            degraded=(how == "CSS selectors" or not from_page),
        )
        return events

    errors.note_suspect(
        "adrian",
        why_not or f"Yodel widget {widget_id}: no events from any strategy — "
                   f"{eventpage.describe_page(soup)}",
    )
    return []


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

        raw_events = _scrape_events()
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
