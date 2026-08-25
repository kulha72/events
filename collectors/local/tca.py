"""
Tecumseh Center for the Arts (TCA) event collector.

TCA sells through VBO Tickets, whose events page is a JavaScript widget: the
listing lives in an iframe pointing at plugin.vbotickets.com. This collector
used to render the whole thing in headless Chromium and wait 15 seconds for
that iframe — which is how it broke, because the wrapper page's `load` event
does not fire inside 15 seconds once reCAPTCHA and a seat-map iframe are on it.

The widget's own list endpoint answers a plain HTTP request with the same
markup the iframe shows, so the browser is now the fallback rather than the
only path:

  1. GET the widget's list fragment directly — no browser, no waiting
  2. Render the wrapper page and read the iframe — only if that fails

Requires (for the fallback only): playwright, with chromium installed.
"""

import re
import uuid
from datetime import date, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from collectors.base import BaseCollector
from collectors.local import eventpage
from models.event import Event, EventCategory

import errors

LOCAL_TZ = ZoneInfo("America/Detroit")

WRAPPER_URL = "https://tecumsehcenterforthearts.vbotickets.com/events"
PLUGIN_HOST = "https://plugin.vbotickets.com"
TICKETS_URL = "https://www.thetca.org/tickets.html"
DEFAULT_LOCATION = "Tecumseh Center for the Arts, 400 N. Maumee St., Tecumseh, MI"

# The widget session the wrapper page was handing out when this was written.
# It is only a starting guess: _resolve_site_key asks the site for a current
# one first, and the browser fallback reads whatever the live iframe uses.
KNOWN_SITE_KEY = "d7b7befb-80ac-4c77-b28c-a23b353a5df7"

_GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_SITE_ID_RE = re.compile(r'var\s+SiteID\s*=\s*"([^"]+)"')
_ORG_ID_RE = re.compile(r'var\s+OrgID\s*=\s*"([^"]+)"')

# How long to wait for the JS widget to render, in the fallback (milliseconds)
_RENDER_TIMEOUT = 45_000

# Problems worth reporting only if nothing else works. A wrapper page that
# would not load is not a failure when the fallback key still returns the
# season; reporting it anyway is how a health block trains you to ignore it.
_problems: list[str] = []

_session = requests.Session()
_session.headers.update({
    "User-Agent": eventpage.BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": WRAPPER_URL,
})


def _list_url(site_key: str) -> str:
    return (f"{PLUGIN_HOST}/Plugin/events/showevents"
            f"?ViewType=list&EventType=current&day=&s={site_key}")


def _resolve_site_key() -> str | None:
    """Ask the wrapper page which widget session to read.

    The wrapper carries the org's SiteID in an inline script; loadplugin turns
    that into the per-session key the list endpoint wants. Hard-coding the key
    would work until VBO rotated it and then fail silently, which is the class
    of bug this collector is being fixed for.
    """
    try:
        resp = _session.get(WRAPPER_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        _problems.append(f"could not read the VBO wrapper page: {e}")
        return None

    site_id = _SITE_ID_RE.search(resp.text)
    if not site_id:
        return None
    org_id = _ORG_ID_RE.search(resp.text)

    params = {
        "siteid": site_id.group(1),
        "page": "ListEvents",
        "w": "1280",
        "h": "720",
        "o": org_id.group(1) if org_id else "0",
        "eid": "0",
        "edid": "0",
        "did": "0",
        "wlid": "0",
    }
    try:
        loaded = _session.get(f"{PLUGIN_HOST}/plugin/loadplugin", params=params, timeout=15)
        loaded.raise_for_status()
    except Exception:
        return None

    found = _GUID_RE.search(loaded.text)
    return found.group(0) if found else None


def _fetch_cards_directly() -> list:
    """Read the widget's list fragment over plain HTTP."""
    keys = []
    resolved = _resolve_site_key()
    if resolved:
        keys.append(resolved)
    if KNOWN_SITE_KEY not in keys:
        keys.append(KNOWN_SITE_KEY)

    for index, key in enumerate(keys):
        try:
            resp = _session.get(_list_url(key), timeout=20)
            resp.raise_for_status()
        except Exception as e:
            _problems.append(f"VBO list fetch failed: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".EventListWrapper")
        if cards:
            errors.note_strategy(
                "tca",
                f"{len(cards)} events from the VBO list endpoint"
                + (" — using the hard-coded widget key" if index else ""),
                # Falling back to the baked-in key means key resolution broke,
                # and the key itself will not last forever.
                degraded=bool(index),
            )
            return cards
    return []


def _scrape_with_playwright() -> list:
    """Render the wrapper page and read the event cards out of the iframe."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        _problems.append("playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    with sync_playwright() as pw:
        browser, page = eventpage.new_browser_page(pw)
        try:
            try:
                # `load` waits on every ad, font and reCAPTCHA frame on the
                # page; the iframe we want is in the DOM long before that.
                page.goto(WRAPPER_URL, timeout=_RENDER_TIMEOUT, wait_until="domcontentloaded")
            except PWTimeout:
                _problems.append(f"VBO wrapper page did not load within {_RENDER_TIMEOUT}ms")
                return []

            try:
                page.wait_for_selector("iframe", timeout=_RENDER_TIMEOUT)
            except PWTimeout:
                _problems.append(f"VBO iframe did not appear within {_RENDER_TIMEOUT}ms")
                return []

            frame = next(
                (f for f in page.frames
                 if "plugin.vbotickets.com" in f.url and f is not page.main_frame),
                None,
            )
            if frame is None:
                _problems.append("could not locate the VBO iframe among the page's frames")
                return []

            try:
                frame.wait_for_selector(".EventListWrapper", timeout=_RENDER_TIMEOUT)
            except PWTimeout:
                _problems.append(f"VBO events did not finish loading within {_RENDER_TIMEOUT}ms")

            soup = BeautifulSoup(frame.content(), "html.parser")
        finally:
            browser.close()

    cards = soup.select(".EventListWrapper")
    if cards:
        errors.note_strategy(
            "tca",
            f"{len(cards)} events from the rendered VBO iframe — the list endpoint did not answer",
            degraded=True,
        )
        return cards

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
    return []


def _parse_cards(cards) -> list[dict]:
    """Turn VBO event cards into raw event dicts."""
    raw_events = []
    for card in cards:
        if isinstance(card, dict):
            # Already normalised by the structured-data fallback.
            if card.get("start_dt"):
                raw_events.append(card)
            continue

        title_el = card.select_one("h2.HeaderEventName a") or card.select_one("h2.HeaderEventName")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        # Date — ".TextEventDate" contains text like "Fri, 3/13/2026 @ 7:30 PM"
        date_el = card.select_one(".TextEventDate")
        start_dt = None
        if date_el:
            date_text = date_el.get_text(" ", strip=True).replace("@", "")
            try:
                start_dt = dateparser.parse(date_text, fuzzy=True)
                if start_dt:
                    start_dt = start_dt.replace(tzinfo=LOCAL_TZ)
            except Exception:
                pass

        if not start_dt:
            continue

        loc_el = card.select_one(".TextVenueName")
        raw_events.append({
            "title": title,
            "start_dt": start_dt,
            # Always point at the TCA tickets page rather than the booking tab.
            "url": TICKETS_URL,
            "location": loc_el.get_text(strip=True) if loc_el else DEFAULT_LOCATION,
        })
    return raw_events


def _scrape_events() -> list[dict]:
    _problems.clear()
    cards = _fetch_cards_directly()
    if not cards:
        cards = _scrape_with_playwright()

    events = _parse_cards(cards)
    if not events:
        for problem in _problems:
            errors.record("tca", problem)
    return events


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

        raw_events = _scrape_events()
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
