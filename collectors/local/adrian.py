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
optional here. It is behind Cloudflare, whose challenge does not clear for a
GitHub runner in any browser setup tried; that is reported as blocked rather
than as a parsing failure.

Because of that wall, two plain-HTTP calendars carry the section:

  - Lenawee District Library's RSS feed (lenawee.lib.mi.us): every branch's
    programs, each with a real start and end time
  - the City of Adrian's Revize calendar (adriancity.com), minus the
    government meetings that make up most of it: First Friday, the City
    Market, the Holiday Market

Yodel is still tried first, so if the wall ever lifts its listings come back.
The wall is then reported as degraded rather than blocked, since the section
is not empty because of it.

Requires: playwright, with chromium installed (for Yodel only).
"""

import html
import json
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil.rrule import rrulestr

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


def _scrape_yodel() -> tuple[list[dict], tuple[str, str] | None]:
    """Events from the Yodel widget, and what to report if it gave none.

    The verdict is `(kind, message)` with kind "failure", "blocked" or
    "suspect", or `("degraded", message)` alongside events read on a fallback
    path; None when it worked cleanly. `_scrape_events` decides what reaches
    the health report, since a wall on Yodel is no longer an empty section.
    """
    widget_id, why_not = _discover_widget_id()
    from_page = bool(widget_id)
    widget_id = widget_id or KNOWN_WIDGET_ID

    url = _widget_url(widget_id)
    try:
        soup = eventpage.render_page(url, wait_selectors=WAIT_SELECTORS, timeout_ms=45_000)
    except Exception as e:
        message = f"could not render the Lenawee events widget: {e}"
        return [], ("failure", f"{why_not}; {message}" if why_not else message)

    events, how = eventpage.extract_all(
        soup, LOCAL_TZ, DEFAULT_LOCATION, eventpage.DEFAULT_ITEM_SELECTORS, url
    )
    if events:
        message = (f"Yodel widget: {len(events)} events from {how}"
                   + ("" if from_page else " — using the hard-coded widget id"))
        # Reading the third-party markup, or guessing which widget, are both a
        # step short of solid.
        if how == "CSS selectors" or not from_page:
            return events, ("degraded", message)
        print(f"  [adrian] via {message}")
        return events, None

    if eventpage.is_challenge_page(soup):
        # Every runner since the switch to Yodel has stopped here, and no
        # browser setup gets through (scripts/local_probe.py yodel-challenge):
        # Cloudflare will not clear a GitHub runner.
        return [], ("blocked", YODEL_BLOCKED)

    return [], ("suspect", why_not or f"Yodel widget {widget_id}: no events from any strategy — "
                                      f"{eventpage.describe_page(soup)}")


YODEL_BLOCKED = (
    "events.yodel.today answers GitHub runners with a Cloudflare challenge "
    "that headless Chromium does not pass"
)


# ── Lenawee District Library: RSS ────────────────────────────────────────────

LIBRARY_FEED_URL = "https://lenawee.lib.mi.us/events/rss/feeds"
_EV_NS = {"ev": "http://purl.org/rss/1.0/modules/event/"}
# Public, but not something anyone plans a weekend around.
_LIBRARY_SKIP_RE = re.compile(r"\bboard meeting\b|\bclosed\b", re.I)


def _parse_library_feed(xml_text: str) -> list[dict]:
    """Raw events from the library's RSS feed.

    Each item's `pubDate` is the event's start with its offset
    ("Sat, 14 Nov 2026 10:00:00 -0500") and `ev:endDate` its end, so unlike
    most calendars here the clock time comes through intact.
    """
    root = ET.fromstring(xml_text)
    events = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        if not title or _LIBRARY_SKIP_RE.search(title):
            continue
        try:
            start = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if start is None:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=LOCAL_TZ)
        end = None
        end_text = item.findtext("ev:endDate", namespaces=_EV_NS)
        if end_text:
            try:
                end = parsedate_to_datetime(end_text)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=LOCAL_TZ)
            except (TypeError, ValueError):
                end = None
        branch = (item.findtext("ev:location", namespaces=_EV_NS) or "").strip()
        events.append({
            "title": title,
            "start_dt": start.astimezone(LOCAL_TZ),
            "end_dt": end.astimezone(LOCAL_TZ) if end else None,
            "all_day": False,
            "location": branch or "Lenawee District Library",
            "url": (item.findtext("link") or "").strip() or LIBRARY_FEED_URL,
        })
    return events


def _library_events() -> list[dict]:
    resp = _session.get(LIBRARY_FEED_URL, timeout=30)
    resp.raise_for_status()
    return _parse_library_feed(resp.text)


# ── City of Adrian: Revize calendar ──────────────────────────────────────────

CITY_CALENDAR_PAGE = "https://www.adriancity.com/calendar.php"
CITY_DATA_URL = (
    "https://www.adriancity.com/_assets_/plugins/revizeCalendar/calendar_data_handler.php"
    "?webspace=adrianmi&relative_revize_url=//cms9.revize.com&protocol=https:"
)
CITY_LOCATION = "Adrian, MI"
# Most of the city's calendar is government business. Keep the public events.
_CITY_SKIP_RE = re.compile(
    r"meeting|board|commission|committee|review|council|hearing|session|"
    r"work\s*study|test\b|yard waste|tree pickup|closed",
    re.I,
)


def _naive_local(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)


def _city_occurrences(doc: dict, start: date, end: date) -> list[datetime]:
    """Local start times of a calendar entry between two dates (inclusive)."""
    first = _naive_local(doc.get("start") or "")
    if first is None:
        return []
    lo = datetime.combine(start, datetime.min.time())
    hi = datetime.combine(end, datetime.max.time())
    rule = doc.get("rrule")
    if rule:
        try:
            return list(rrulestr(rule, forceset=True, dtstart=first).between(lo, hi, inc=True))
        except (ValueError, TypeError):
            return [first] if lo <= first <= hi else []
    return [first] if lo <= first <= hi else []


def _parse_city_calendar(docs: list, today: date, lookahead_days: int) -> list[dict]:
    """Raw events from the city's calendar JSON, recurrences expanded."""
    window_end = today + timedelta(days=lookahead_days)
    events = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        title = html.unescape(str(doc.get("title") or "")).strip()
        if not title or _CITY_SKIP_RE.search(title):
            continue
        first = _naive_local(doc.get("start") or "")
        last = _naive_local(doc.get("end") or "")
        length = (last - first) if first and last and last > first else None
        all_day = bool(doc.get("allDay"))
        url = str(doc.get("url") or "").strip()
        if not url.startswith("http"):
            url = CITY_CALENDAR_PAGE
        location = html.unescape(str(doc.get("location") or "")).strip()

        for when in _city_occurrences(doc, today - timedelta(days=1), window_end):
            start_dt = when.replace(tzinfo=LOCAL_TZ)
            if all_day:
                start_dt = start_dt.replace(hour=0, minute=0, second=0)
            events.append({
                "title": title,
                "start_dt": start_dt,
                "end_dt": (start_dt + length) if length and not all_day else None,
                "all_day": all_day,
                "location": location or CITY_LOCATION,
                # The url must differ per occurrence or the id would collide;
                # the start is folded into the id below, so a shared url is fine.
                "url": url,
            })
    return events


def _city_events(today: date, lookahead_days: int) -> list[dict]:
    resp = _session.get(CITY_DATA_URL, timeout=45)
    resp.raise_for_status()
    return _parse_city_calendar(json.loads(resp.text), today, lookahead_days)


# ── All three together ───────────────────────────────────────────────────────

def _dedupe(events: list[dict]) -> list[dict]:
    seen, kept = set(), []
    for ev in events:
        key = (ev["title"].casefold(), ev["start_dt"].astimezone(timezone.utc))
        if key in seen:
            continue
        seen.add(key)
        kept.append(ev)
    return kept


def _scrape_events(today: date | None = None, lookahead_days: int = 7) -> list[dict]:
    today = today or datetime.now(LOCAL_TZ).date()

    yodel_events, verdict = _scrape_yodel()

    other: dict[str, list[dict]] = {}
    problems: list[str] = []
    for label, fetch in (
        ("library feed", _library_events),
        ("city calendar", lambda: _city_events(today, lookahead_days)),
    ):
        try:
            other[label] = fetch()
        except Exception as e:
            problems.append(f"{label}: {e}")
            other[label] = []

    events = _dedupe(yodel_events + [e for evs in other.values() for e in evs])
    backups_ok = len(problems) < len(other)

    if not backups_ok and not yodel_events:
        # Nothing worked. The backups failing is the actionable part (Yodel
        # has been walled for months), but keep its diagnosis in the line.
        why = f"Yodel {verdict[0]}: {verdict[1]}" if verdict else "Yodel gave nothing"
        errors.record("adrian", f"every Lenawee calendar failed — {'; '.join(problems)}; {why}")
        return events

    # One backup down while the other works is still worth a line of its own.
    for problem in problems:
        errors.record("adrian", f"Lenawee {problem}")

    if verdict is None:
        return events

    kind, message = verdict
    if kind == "degraded":
        errors.note_strategy("adrian", message, degraded=True)
        return events

    counts = ", ".join(f"{len(v)} from the {k}" for k, v in other.items() if v) or "none yet"
    errors.note_strategy(
        "adrian",
        f"Yodel gave nothing ({kind}: {message}) — {counts}",
        degraded=True,
    )
    return events


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
                # Yodel publishes a date-only startDate for its all-day
                # listings; without carrying that through they all arrive in
                # the digest announced at 12 AM.
                all_day=raw.get("all_day", False),
                location=raw.get("location") or DEFAULT_LOCATION,
                source="adrian",
                url=raw.get("url") or BASE_URL,
                tags=["local", "adrian", "lenawee"],
            ))

        return eventpage.within_window(events, today, lookahead_days, LOCAL_TZ, "adrian")
