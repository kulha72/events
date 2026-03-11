"""
Tecumseh collector — adapts the existing scrape_events.py scraper to
return a list[Event] via the BaseCollector interface.
"""

import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta

# Allow importing the parent-directory scraper for shared helpers.
# If running daily-digest as a standalone package this path manipulation
# lets us reuse the already-tested parsing logic without duplicating it.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _ROOT)

from collectors.base import BaseCollector
from models.event import Event, EventCategory, EventPriority

LOCAL_TZ = ZoneInfo("America/Detroit")

DOWNTOWN_URL = "https://www.downtowntecumseh.com/events/"
HERALD_BASE = "https://www.tecumsehherald.com"
HERALD_CALENDAR_PATH = "/calendar-node-field-date/month"

_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
})

# ── Regex helpers (same as the original scraper) ─────────────────────────────

_MONTHS = (
    "January|February|March|April|May|June|"
    "July|August|September|October|November|December"
)
_DAYS_OF_WEEK = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2}(?::\d{2})?(?:am|pm))\s*(?:[-–]|to)\s*(\d{1,2}(?::\d{2})?(?:am|pm))",
    re.IGNORECASE,
)
_SINGLE_TIME_RE = re.compile(r"\b(\d{1,2}(?::\d{2})?(?:am|pm))\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_ORDINAL_RE = re.compile(r"(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)
_DAY_RANGE_RE = re.compile(
    rf"({_MONTHS})\s+(\d{{1,2}})-(\d{{1,2}}),?\s*(20\d{{2}})?", re.IGNORECASE
)
_AMP_DAYS_RE = re.compile(
    rf"({_MONTHS})\s+(\d{{1,2}})\s*&\s*(\d{{1,2}}),?\s*(20\d{{2}})?", re.IGNORECASE
)
_DATE_IN_LINE_RE = re.compile(
    rf"(?:(?:{_DAYS_OF_WEEK}),?\s+)?({_MONTHS})\s+(\d{{1,2}}),?\s*(20\d{{2}})?",
    re.IGNORECASE,
)
_HERALD_SKIP_RE = re.compile(
    r"classifieds|privacy policy|refund policy|terms of (service|use)|advertis",
    re.IGNORECASE,
)
_HERALD_DT_RE = re.compile(
    r"(?:\w+,\s+)?(\w+ \d{1,2},\s+\d{4}),?\s+from\s+"
    r"(\d{1,2}(?::\d{2})?(?:am|pm))\s+to\s+(\d{1,2}(?::\d{2})?(?:am|pm))",
    re.IGNORECASE,
)
_HERALD_DATE_ONLY_RE = re.compile(r"(?:\w+,\s+)?(\w+ \d{1,2},\s+\d{4})", re.IGNORECASE)


# ── Utility ───────────────────────────────────────────────────────────────────

def _fetch(url: str) -> BeautifulSoup:
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _line_times(line: str):
    tm = _TIME_RANGE_RE.search(line)
    if tm:
        return tm.group(1), tm.group(2)
    sm = _SINGLE_TIME_RE.search(line)
    if sm:
        return sm.group(1), None
    return None, None


def _parse_time_str(time_str: str, ref_date: date):
    if not time_str:
        return None
    try:
        dt = dateparser.parse(f"{ref_date.strftime('%Y-%m-%d')} {time_str}")
        return dt.replace(tzinfo=LOCAL_TZ)
    except Exception:
        return None


def _parse_downtown_when(when_text: str) -> list[dict]:
    text = _ORDINAL_RE.sub(r"\1", when_text.strip())
    year_m = _YEAR_RE.search(text)
    year = int(year_m.group(1)) if year_m else date.today().year
    results = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for line in lines:
        dr = _DAY_RANGE_RE.search(line)
        if dr:
            month_str, d1, d2 = dr.group(1), int(dr.group(2)), int(dr.group(3))
            yr = int(dr.group(4)) if dr.group(4) else year
            try:
                start_d = dateparser.parse(f"{month_str} {d1}, {yr}").date()
                end_d = dateparser.parse(f"{month_str} {d2}, {yr}").date()
                start_t, end_t = _line_times(line)
                results.append({"start_date": start_d, "end_date": end_d,
                                 "start_time_str": start_t, "end_time_str": end_t})
            except Exception:
                pass
            continue

        am = _AMP_DAYS_RE.search(line)
        if am:
            month_str, d1, d2 = am.group(1), int(am.group(2)), int(am.group(3))
            yr = int(am.group(4)) if am.group(4) else year
            start_t, end_t = _line_times(line)
            for d_num in (d1, d2):
                try:
                    d = dateparser.parse(f"{month_str} {d_num}, {yr}").date()
                    results.append({"start_date": d, "end_date": None,
                                    "start_time_str": start_t, "end_time_str": end_t})
                except Exception:
                    pass
            continue

        dm = _DATE_IN_LINE_RE.search(line)
        if dm:
            month_str, day_str = dm.group(1), dm.group(2)
            yr = int(dm.group(3)) if dm.group(3) else year
            try:
                d = dateparser.parse(f"{month_str} {day_str}, {yr}").date()
            except Exception:
                continue
            start_t, end_t = _line_times(line)
            results.append({"start_date": d, "end_date": None,
                            "start_time_str": start_t, "end_time_str": end_t})
            continue

        if results and not results[-1]["start_time_str"]:
            start_t, end_t = _line_times(line)
            if start_t:
                results[-1]["start_time_str"] = start_t
                results[-1]["end_time_str"] = end_t

    return results


# ── Scraping functions ────────────────────────────────────────────────────────

def _scrape_downtown() -> list[dict]:
    events = []
    soup = _fetch(DOWNTOWN_URL)
    for event_div in soup.find_all("div", class_="event"):
        title_tag = event_div.select_one(".event__title h3") or event_div.select_one(".event__title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue

        time_div = event_div.select_one(".event__time")
        when_text = where_text = ""
        if time_div:
            for p in time_div.find_all("p"):
                strong = p.find("strong")
                if not strong:
                    continue
                label = strong.get_text(strip=True).upper()
                p_text = re.sub(r"^(?:WHEN|WHERE):\s*", "", p.get_text(strip=True), flags=re.IGNORECASE).strip()
                if "WHEN" in label and not when_text:
                    when_text = p_text
                elif "WHERE" in label and not where_text:
                    where_text = p_text

        if not when_text:
            continue

        desc_div = event_div.select_one(".event__desc")
        description = ""
        if desc_div:
            paras = [p.get_text(strip=True) for p in desc_div.find_all("p") if p.get_text(strip=True)]
            description = " ".join(paras)

        detail_url = DOWNTOWN_URL
        if desc_div:
            link = desc_div.find("a", href=True)
            if link:
                href = link["href"]
                if not href.startswith("http"):
                    href = "https://www.downtowntecumseh.com" + href
                detail_url = href

        for entry in _parse_downtown_when(when_text):
            events.append({
                "title": title,
                "location": where_text,
                "description": description,
                "url": detail_url,
                "source": "downtown_tecumseh",
                **entry,
            })
    return events


def _scrape_herald(months_ahead: int = 3) -> list[dict]:
    events = []
    seen_paths: set[str] = set()
    today = date.today()

    for i in range(months_ahead + 1):
        month_date = today + relativedelta(months=i)
        cal_url = f"{HERALD_BASE}{HERALD_CALENDAR_PATH}/{month_date.strftime('%Y-%m')}"
        try:
            soup = _fetch(cal_url)
        except Exception:
            continue

        for a in soup.find_all("a", href=True):
            path = a["href"]
            if path.startswith("/content/") and path not in seen_paths:
                seen_paths.add(path)
                event = _parse_herald_event_page(HERALD_BASE + path)
                if event:
                    events.append(event)
    return events


def _parse_herald_event_page(url: str) -> dict | None:
    try:
        soup = _fetch(url)
    except Exception:
        return None

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "Herald Event"
    if _HERALD_SKIP_RE.search(title):
        return None

    full_text = soup.get_text(" ", strip=True)
    start_dt = end_dt = None

    m = _HERALD_DT_RE.search(full_text)
    if m:
        date_str, start_t, end_t = m.group(1), m.group(2), m.group(3)
        try:
            start_dt = dateparser.parse(f"{date_str} {start_t}").replace(tzinfo=LOCAL_TZ)
            end_dt = dateparser.parse(f"{date_str} {end_t}").replace(tzinfo=LOCAL_TZ)
        except Exception:
            pass

    if not start_dt:
        m = _HERALD_DATE_ONLY_RE.search(full_text)
        if m:
            try:
                start_dt = dateparser.parse(m.group(1)).date()
            except Exception:
                pass

    if not start_dt:
        return None

    start_date = start_dt if isinstance(start_dt, date) and not isinstance(start_dt, datetime) else start_dt.date()
    if start_date < date.today() - timedelta(days=7):
        return None

    description = ""
    for selector in (".field-items", ".node-content", "article", ".content"):
        el = soup.select_one(selector)
        if el:
            paras = [p.get_text(strip=True) for p in el.find_all("p") if p.get_text(strip=True)]
            description = " ".join(paras)
            if description:
                break

    location = ""
    loc_m = re.search(r"(?:Location|Where|Venue)[:\s]+([^\n.]{3,80})", full_text, re.IGNORECASE)
    if loc_m:
        location = loc_m.group(1).strip()

    return {
        "title": title,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "location": location,
        "description": description,
        "url": url,
        "source": "tecumseh_herald",
    }


# ── Converter ─────────────────────────────────────────────────────────────────

def _to_utc(dt) -> datetime:
    """Convert a tz-aware local datetime or date to a UTC datetime."""
    from datetime import timezone
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc)
    # All-day: use midnight local time
    midnight = datetime(dt.year, dt.month, dt.day, tzinfo=LOCAL_TZ)
    return midnight.astimezone(timezone.utc)


# ── Collector ─────────────────────────────────────────────────────────────────

class TecumsehCollector(BaseCollector):

    def __init__(self, config: dict):
        self.config = config

    @property
    def source_name(self) -> str:
        return "tecumseh"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        import cache

        cached = cache.get("tecumseh", ttl_seconds=3600 * 12)
        if cached:
            return [Event(**e) for e in cached]

        cutoff = today + timedelta(days=lookahead_days)
        events: list[Event] = []

        for raw in _scrape_downtown():
            sd = raw["start_date"]
            if sd < today - timedelta(days=1) or sd > cutoff:
                continue
            if raw["start_time_str"]:
                start = _parse_time_str(raw["start_time_str"], sd)
                end = _parse_time_str(raw["end_time_str"], sd) if raw["end_time_str"] else None
            else:
                start = datetime(sd.year, sd.month, sd.day, tzinfo=LOCAL_TZ)
                end = None
                if raw.get("end_date"):
                    ed = raw["end_date"]
                    end = datetime(ed.year, ed.month, ed.day, 23, 59, tzinfo=LOCAL_TZ)

            if not start:
                continue

            events.append(Event(
                id=f"tecumseh:downtown:{uuid.uuid5(uuid.NAMESPACE_URL, raw['url'] + str(sd))}",
                title=raw["title"],
                category=EventCategory.LOCAL,
                start=_to_utc(start),
                end=_to_utc(end) if end else None,
                location=raw.get("location") or "Downtown Tecumseh",
                source="downtown_tecumseh",
                url=raw.get("url"),
                tags=["local", "tecumseh"],
            ))

        for raw in _scrape_herald(months_ahead=3):
            dt = raw["start_dt"]
            sd = dt if isinstance(dt, date) and not isinstance(dt, datetime) else dt.date()
            if sd < today - timedelta(days=1) or sd > cutoff:
                continue
            start_utc = _to_utc(dt)
            end_utc = _to_utc(raw["end_dt"]) if raw.get("end_dt") else None

            events.append(Event(
                id=f"tecumseh:herald:{uuid.uuid5(uuid.NAMESPACE_URL, raw['url'])}",
                title=raw["title"],
                category=EventCategory.LOCAL,
                start=start_utc,
                end=end_utc,
                location=raw.get("location") or "Tecumseh, MI",
                source="tecumseh_herald",
                url=raw.get("url"),
                tags=["local", "tecumseh"],
            ))

        return events
