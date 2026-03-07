"""
Ann Arbor local events collector.
Scrapes visitannarbor.org events calendar.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from collectors.base import BaseCollector
from models.event import Event, EventCategory, EventPriority

LOCAL_TZ = ZoneInfo("America/Detroit")
BASE_URL = "https://www.visitannarbor.org/events/"

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; daily-digest-annarbor/1.0)"})


def _fetch_page(url: str) -> BeautifulSoup:
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _scrape_events(today: date, lookahead_days: int) -> list[dict]:
    """Scrape visitannarbor.org/events for upcoming events."""
    cutoff = today + timedelta(days=lookahead_days)
    events = []

    # The visitor bureau calendar paginates by date range via query params
    start_str = today.strftime("%Y-%m-%d")
    end_str = cutoff.strftime("%Y-%m-%d")
    url = f"{BASE_URL}?startdate={start_str}&enddate={end_str}"

    try:
        soup = _fetch_page(url)
    except Exception as e:
        print(f"  [annarbor] Warning: could not fetch {url}: {e}")
        return []

    # visitannarbor.org uses .tribe-event-list items or similar
    # Try multiple known selectors for event listing pages
    items = (
        soup.select(".tribe-events-calendar article") or
        soup.select(".tribe-event-list-item") or
        soup.select(".wp-block-tribe-event-list .tribe-event") or
        soup.select("article.type-tribe_events") or
        soup.select(".tribe-events-loop .tribe-event-schedule-details")
    )

    for item in items:
        # Title
        title_el = item.select_one("h2 a, h3 a, .tribe-event-url")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        url_val = title_el.get("href", BASE_URL)

        # Date/time — look for datetime attrs or text
        time_el = item.select_one("time[datetime], abbr[title]")
        start_dt = end_dt = None
        if time_el:
            dt_str = time_el.get("datetime") or time_el.get("title", "")
            try:
                start_dt = dateparser.parse(dt_str).replace(tzinfo=LOCAL_TZ)
            except Exception:
                pass

        if not start_dt:
            # Fall back to visible date text
            date_el = item.select_one(".tribe-event-schedule-details, .tribe-events-schedule")
            if date_el:
                try:
                    start_dt = dateparser.parse(date_el.get_text(strip=True), fuzzy=True).replace(tzinfo=LOCAL_TZ)
                except Exception:
                    pass

        if not start_dt:
            continue

        # Location
        loc_el = item.select_one(".tribe-venue, .tribe-events-venue-details")
        location = loc_el.get_text(strip=True) if loc_el else "Ann Arbor, MI"

        events.append({
            "title": title,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "location": location,
            "url": url_val,
        })

    return events


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
                location=raw.get("location") or "Ann Arbor, MI",
                source="annarbor",
                url=raw.get("url"),
                tags=["local", "ann-arbor"],
            ))

        return events
