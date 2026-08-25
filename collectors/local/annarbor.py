"""
Ann Arbor local events collector.

visitannarbor.org redirects to annarbor.org, a Simpleview destination site.
Its calendar is not in the page: the page ships an empty list and fills it
from a private JSON API, `/includes/rest_v2/plugins_events_events_by_date/`,
signed with a token the page holds. Which is why the old scraper fetched a
perfectly good 5,000-character page and found nothing on it — there was
nothing on it.

The API refuses `requests` outright ("Invalid credentials" without a token,
"Access Denied" with the page's own token), so the way in is the page itself:

  1. Render the calendar, keep the JSON it fetches for its own first screen
  2. Ask the same API again from inside the page, for the whole digest window
  3. If both come up empty, fall back to reading the rendered markup

Requires: playwright, with chromium installed.
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

from collectors.base import BaseCollector
from collectors.local import eventpage
from models.event import Event, EventCategory

import errors

LOCAL_TZ = ZoneInfo("America/Detroit")
BASE_URL = "https://www.visitannarbor.org/events/"
SITE_ROOT = "https://www.annarbor.org"
DEFAULT_LOCATION = "Ann Arbor, MI"

# The API call the calendar makes for its own first screen.
_API_PATTERN = r"plugins_events_events_by_date"

# What to wait for: any of these means the calendar has drawn something.
WAIT_SELECTORS = (
    "[class*='event-item']",
    ".listing-item",
    "article[class*='event']",
)

# Ask the page to re-run its own query, widened to the digest window. Running
# it here rather than from Python is the whole point: the fetch inherits the
# page's origin, cookies and token, none of which we have to know or store.
_WIDER_QUERY_JS = """
async ([startIso, endIso]) => {
  const token = (typeof core !== 'undefined' && core && core.simpleToken)
    ? core.simpleToken
    : (window.core && window.core.simpleToken);
  if (!token) return null;
  const url = new URL(
    `${window.location.protocol}//${window.location.host}` +
    `/includes/rest_v2/plugins_events_events_by_date/find/`
  );
  url.searchParams.append('json', JSON.stringify({
    filter: {
      active: true,
      date_range: { start: { $date: startIso }, end: { $date: endIso } },
    },
    options: {
      limit: 200,
      skip: 0,
      count: true,
      castDocs: false,
      fields: {
        _id: 1, location: 1, date: 1, startDate: 1, endDate: 1,
        recurrence: 1, recurType: 1, recid: 1, title: 1, url: 1,
        categories: 1, city: 1, region: 1, admission: 1,
        'listing.title': 1, 'listing.url': 1, 'listing.city': 1,
      },
      hooks: [],
      sort: { date: 1, rank: 1, title_sort: 1 },
    },
  }));
  url.searchParams.append('token', token);
  const res = await fetch(url, { method: 'GET' });
  if (!res.ok) return null;
  return await res.json();
}
"""


def _docs_of(payload) -> list[dict]:
    """Unwrap the API's `{"docs": {"count": n, "docs": [...]}}` envelope."""
    if not isinstance(payload, dict):
        return []
    docs = payload.get("docs")
    if isinstance(docs, dict):
        docs = docs.get("docs")
    return [d for d in (docs or []) if isinstance(d, dict)]


def _local_day(value) -> date | None:
    """Read the occurrence day out of a Simpleview timestamp.

    `date` is the end of the occurrence's local day expressed in UTC
    ("2026-08-26T03:59:59Z" is the 25th in Detroit), so the local date is the
    only part of it worth keeping.
    """
    if not value:
        return None
    try:
        parsed = dateparser.parse(str(value))
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(LOCAL_TZ).date()


def _events_from_docs(docs: list[dict]) -> list[dict]:
    """Turn API docs into raw event dicts, one per occurrence."""
    events = []
    seen = set()
    for doc in docs:
        title = str(doc.get("title") or "").strip()
        day = _local_day(doc.get("date")) or _local_day(doc.get("startDate"))
        if not title or not day:
            continue
        key = (title, day)
        if key in seen:
            continue
        seen.add(key)

        listing = doc.get("listing") if isinstance(doc.get("listing"), dict) else {}
        location = str(doc.get("location") or listing.get("title") or "").strip()
        city = str(doc.get("city") or listing.get("city") or "").strip()
        if location and city and city not in location:
            location = f"{location}, {city}"

        url = str(doc.get("url") or "").strip()
        if url.startswith("/"):
            url = SITE_ROOT + url

        # The feed carries no time of day, so these are all-day listings.
        events.append({
            "title": title,
            "start_dt": datetime.combine(day, time.min, tzinfo=LOCAL_TZ),
            "end_dt": None,
            "location": location or DEFAULT_LOCATION,
            "url": url or BASE_URL,
        })
    return events


def _scrape_events(today: date, lookahead_days: int) -> list[dict]:
    cutoff = today + timedelta(days=lookahead_days)
    window = [f"{today.isoformat()}T00:00:00.000Z", f"{cutoff.isoformat()}T23:59:59.000Z"]

    try:
        soup, payloads, widened = eventpage.render_page_capturing(
            BASE_URL,
            capture_pattern=_API_PATTERN,
            wait_selectors=WAIT_SELECTORS,
            evaluate=_WIDER_QUERY_JS.strip(),
            evaluate_args=window,
        )
    except Exception as e:
        errors.record("annarbor", f"could not render annarbor.org/events: {e}")
        return []

    # The page's own call only asks for its first screen; the widened one
    # covers the digest window. Both are the same API, so merge and dedupe.
    docs = _docs_of(widened)
    from_page = [d for p in payloads for d in _docs_of(p)]
    if docs:
        events = _events_from_docs(docs + from_page)
        errors.note_strategy("annarbor", f"annarbor.org: {len(events)} events from the events API")
        return events

    if from_page:
        events = _events_from_docs(from_page)
        errors.note_strategy(
            "annarbor",
            f"annarbor.org: {len(events)} events from the API's first screen only "
            f"— the widened query was refused",
            degraded=True,
        )
        return events

    events, how = eventpage.extract_all(
        soup, LOCAL_TZ, DEFAULT_LOCATION, eventpage.DEFAULT_ITEM_SELECTORS, BASE_URL
    )
    if events:
        errors.note_strategy(
            "annarbor",
            f"annarbor.org: {len(events)} events from {how} — the events API answered nothing",
            degraded=True,
        )
        return events

    errors.note_suspect(
        "annarbor",
        f"annarbor.org: the events API returned nothing and the page parsed to nothing "
        f"— {eventpage.describe_page(soup)}",
    )
    return []


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
