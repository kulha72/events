"""
Tecumseh Center for the Arts (TCA) event collector.

TCA uses VBO Tickets (vbotickets.com), which is a fully JavaScript-rendered
ticketing platform — there is no public API or iCal feed.  This collector
uses Playwright (headless Chromium) to render the events listing page and
extract event data from the DOM.

Requires: playwright (install with `pip install playwright && playwright install chromium`)
"""

import uuid
from datetime import date, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

from collectors.base import BaseCollector
from collectors.local import eventpage
from models.event import Event, EventCategory

import errors

LOCAL_TZ = ZoneInfo("America/Detroit")

EVENTS_URL = "https://tecumsehcenterforthearts.vbotickets.com/events"
TICKETS_URL = "https://www.thetca.org/tickets.html"
DEFAULT_LOCATION = "Tecumseh Center for the Arts, 400 N. Maumee St., Tecumseh, MI"

# How long to wait for the JS widget to render (milliseconds)
_RENDER_TIMEOUT = 15_000


def _scrape_with_playwright() -> list[dict]:
    """Launch headless Chromium, render the VBO events page, return raw event dicts."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        errors.record("tca", "playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    raw_events = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (compatible; daily-digest-tca/1.0)")

        try:
            page.goto(EVENTS_URL, timeout=_RENDER_TIMEOUT)
            # Events live inside an iframe (#MyEventWrapper) pointing to plugin.vbotickets.com
            page.wait_for_selector("#MyEventWrapper", timeout=_RENDER_TIMEOUT)
        except PWTimeout:
            errors.record("tca", f"iframe did not appear within {_RENDER_TIMEOUT}ms")
            browser.close()
            return []

        # Get the iframe and wait for its content to render
        frame = page.frame(name="MyEventWrapper")
        if frame is None:
            # Fallback: find by URL pattern
            for f in page.frames:
                if "vbotickets.com" in f.url and f != page.main_frame:
                    frame = f
                    break

        if frame is None:
            errors.record("tca", "could not locate VBO iframe")
            browser.close()
            return []

        # Wait for at least one event card to appear
        try:
            frame.wait_for_selector(".EventListWrapper", timeout=_RENDER_TIMEOUT)
        except PWTimeout:
            errors.record("tca", f"events did not finish loading within {_RENDER_TIMEOUT}ms")

        # Parse iframe content with BeautifulSoup
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(frame.content(), "html.parser")
        browser.close()

    # VBO renders one div.EventListWrapper per event
    cards = soup.select(".EventListWrapper")
    if not cards:
        # Before blaming VBO's markup, try the layout-independent layers — the
        # widget emits schema.org data for its own SEO.
        fallback, how = eventpage.extract_all(
            soup, LOCAL_TZ, DEFAULT_LOCATION, eventpage.DEFAULT_ITEM_SELECTORS, TICKETS_URL
        )
        if fallback:
            errors.note_strategy(
                "tca",
                f"{len(fallback)} events from {how} — .EventListWrapper no longer matches",
                degraded=True,
            )
            return [{
                "title": e["title"],
                "start_dt": e["start_dt"],
                "url": TICKETS_URL,
                "location": e.get("location") or DEFAULT_LOCATION,
            } for e in fallback]

        errors.note_suspect(
            "tca",
            f"VBO iframe: no .EventListWrapper card and no structured event data — "
            f"{eventpage.describe_page(soup)}",
        )

    for card in cards:
        # Title
        title_el = card.select_one("h2.HeaderEventName a")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        # Link — always point to the TCA tickets page rather than the vbotickets booking tab
        url = TICKETS_URL

        # Date — ".TextEventDate" contains text like "Fri, 3/13/2026 @ 7:30 PM"
        date_el = card.select_one(".TextEventDate")
        start_dt = None
        if date_el:
            date_text = date_el.get_text(strip=True).replace("@", "")
            try:
                start_dt = dateparser.parse(date_text, fuzzy=True)
                if start_dt:
                    start_dt = start_dt.replace(tzinfo=LOCAL_TZ)
            except Exception:
                pass

        if not start_dt:
            continue

        # Location
        loc_el = card.select_one(".TextVenueName")
        location = loc_el.get_text(strip=True) if loc_el else DEFAULT_LOCATION

        raw_events.append({
            "title": title,
            "start_dt": start_dt,
            "url": url,
            "location": location,
        })

    return raw_events


class TCACollector(BaseCollector):

    def __init__(self, config: dict):
        self.config = config

    @property
    def source_name(self) -> str:
        return "tca"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        import cache

        cached = cache.get("tca", ttl_seconds=3600 * 12)
        if cached:
            return [Event(**e) for e in cached]

        raw_events = _scrape_with_playwright()
        events: list[Event] = []

        for raw in raw_events:
            sd = raw["start_dt"].date()
            start_utc = raw["start_dt"].astimezone(timezone.utc)

            events.append(Event(
                id=f"tca:{uuid.uuid5(uuid.NAMESPACE_URL, raw['url'] + str(sd))}",
                title=raw["title"],
                category=EventCategory.LOCAL,
                start=start_utc,
                end=None,
                location=raw.get("location") or DEFAULT_LOCATION,
                source="tca",
                url=raw.get("url"),
                tags=["local", "tecumseh", "tca", "arts"],
            ))

        # A theatre books months out, so "on sale but nothing this week" is its
        # normal state — and it used to render as an unexplained empty source.
        return eventpage.within_window(events, today, lookahead_days, LOCAL_TZ, "tca")
