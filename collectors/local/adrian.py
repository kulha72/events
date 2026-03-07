"""
Adrian / Lenawee County local events collector.
Scrapes visitlenawee.com events calendar.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from collectors.base import BaseCollector
from models.event import Event, EventCategory

LOCAL_TZ = ZoneInfo("America/Detroit")
BASE_URL = "https://www.visitlenawee.com/events/"

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; daily-digest-adrian/1.0)"})


def _fetch_page(url: str) -> BeautifulSoup:
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _scrape_events(today: date, lookahead_days: int) -> list[dict]:
    cutoff = today + timedelta(days=lookahead_days)
    events = []

    start_str = today.strftime("%Y-%m-%d")
    end_str = cutoff.strftime("%Y-%m-%d")
    url = f"{BASE_URL}?startdate={start_str}&enddate={end_str}"

    try:
        soup = _fetch_page(url)
    except Exception as e:
        print(f"  [adrian] Warning: could not fetch {url}: {e}")
        return []

    items = (
        soup.select("article.type-tribe_events") or
        soup.select(".tribe-events-loop .tribe-events-calendar-list__event") or
        soup.select(".tribe-event-list-item") or
        soup.select(".tribe-events-loop article")
    )

    for item in items:
        title_el = item.select_one("h2 a, h3 a, .tribe-event-url")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        url_val = title_el.get("href", BASE_URL)

        time_el = item.select_one("time[datetime], abbr[title]")
        start_dt = None
        if time_el:
            dt_str = time_el.get("datetime") or time_el.get("title", "")
            try:
                start_dt = dateparser.parse(dt_str).replace(tzinfo=LOCAL_TZ)
            except Exception:
                pass

        if not start_dt:
            date_el = item.select_one(".tribe-event-schedule-details, .tribe-events-schedule")
            if date_el:
                try:
                    start_dt = dateparser.parse(date_el.get_text(strip=True), fuzzy=True).replace(tzinfo=LOCAL_TZ)
                except Exception:
                    pass

        if not start_dt:
            continue

        loc_el = item.select_one(".tribe-venue, .tribe-events-venue-details")
        location = loc_el.get_text(strip=True) if loc_el else "Adrian, MI"

        events.append({
            "title": title,
            "start_dt": start_dt,
            "end_dt": None,
            "location": location,
            "url": url_val,
        })

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

            events.append(Event(
                id=f"adrian:{uuid.uuid5(uuid.NAMESPACE_URL, raw['url'] + str(raw['start_dt']))}",
                title=raw["title"],
                category=EventCategory.LOCAL,
                start=start_utc,
                end=None,
                location=raw.get("location") or "Adrian, MI",
                source="adrian",
                url=raw.get("url"),
                tags=["local", "adrian", "lenawee"],
            ))

        return events
