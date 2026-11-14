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

  1. Render the calendar and keep the signed call it makes for its first screen
  2. Hand that URL back to the page to replay with a later offset
  3. If both come up empty, fall back to reading the rendered markup

Requires: playwright, with chromium installed.
"""

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
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

# The page signs its API call with a token it puts nowhere we can read: not a
# global, not in the resource timings, and not through window.fetch or
# XMLHttpRequest — all three were tried and caught nothing while the browser's
# own network log saw the call every time. So take the signed URL from the
# response we captured, and hand it back to the page to page through.
# Page through the calendar using the query the page itself made, changing
# nothing but the offset.
#
# Building our own filter and options — a wider date range, a bigger limit —
# gets a 403 from the site's WAF even with the page's token and from inside
# the page. Replaying its exact query and only advancing `skip` is the same
# request the calendar makes when a reader scrolls, so it is answered.
_PAGE_THROUGH_JS = """
async ([seen, cutoffIso, maxPages]) => {
  if (!seen) return { error: 'the page made no API call to borrow' };

  let base, payload;
  try {
    base = new URL(seen);
    payload = JSON.parse(base.searchParams.get('json'));
  } catch (e) {
    return { error: `could not read the page's own query: ${e}` };
  }
  const limit = (payload.options && payload.options.limit) || 12;
  const collected = [];

  for (let page = 1; page <= maxPages; page++) {
    payload.options.skip = limit * page;
    const next = new URL(base.toString());
    next.searchParams.set('json', JSON.stringify(payload));

    let res;
    try {
      res = await fetch(next, { method: 'GET' });
    } catch (e) {
      return { error: `fetch threw: ${e}`, data: { docs: { docs: collected } } };
    }
    if (!res.ok) {
      return { error: `status ${res.status} on page ${page}`,
               data: { docs: { docs: collected } } };
    }

    let docs;
    try {
      const body = await res.json();
      docs = (body.docs && body.docs.docs) || [];
    } catch (e) {
      return { error: `not json: ${e}`, data: { docs: { docs: collected } } };
    }
    if (!docs.length) break;

    collected.push(...docs);
    const last = docs[docs.length - 1].date;
    // Sorted by date ascending, so once a page ends past the digest window
    // there is nothing further worth asking for.
    if (last && last > cutoffIso) break;
  }

  return { data: { docs: { docs: collected } } };
}
"""

# How many extra screens to walk before giving up on covering the window.
_MAX_EXTRA_PAGES = 12


def _first_query(captured: list) -> str:
    """The URL of the signed call the page made, if it made one."""
    for capture in captured:
        url = capture.get("url") if isinstance(capture, dict) else ""
        if url:
            return url
    return ""


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


def _doc_start(doc: dict, day: date):
    """The occurrence's start, and whether the feed gave a clock time for it.

    `date` is only ever the end of the local day, so it says nothing about
    when the event begins. A listing that carries a separate `startTime`, or a
    `startDate` at a real local time, gets it; the rest are all-day here and
    `_add_times` looks them up on the event's page.
    """
    for key in ("startTime", "start_time", "starttime"):
        raw = doc.get(key)
        if not raw:
            continue
        start, all_day = eventpage.parse_start(raw, LOCAL_TZ, reference=day)
        if start and not all_day:
            return start.replace(year=day.year, month=day.month, day=day.day), False

    # `startDate` is a timestamp ("2026-11-14T05:00:00.000Z"), but on every
    # listing seen it is local midnight: the day the event began, not a clock
    # time. Reading it as one filed events at 12 AM (1 AM in summer, for a
    # series that began in winter). Only a local time other than midnight is
    # taken as a real start; otherwise the event page supplies it later.
    start, all_day = eventpage.parse_start(doc.get("startDate"), LOCAL_TZ, reference=day)
    if start and not all_day:
        local = start.astimezone(LOCAL_TZ)
        if local.time() != time.min:
            return datetime.combine(day, local.time().replace(tzinfo=None), tzinfo=LOCAL_TZ), False

    # Nothing named a time, so this is an all-day listing.
    return datetime.combine(day, time.min, tzinfo=LOCAL_TZ), True


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

        start_dt, all_day = _doc_start(doc, day)
        events.append({
            "title": title,
            "start_dt": start_dt,
            "end_dt": None,
            "all_day": all_day,
            "location": location or DEFAULT_LOCATION,
            "url": url or BASE_URL,
        })
    return events


# ── Start times from each event's own page ───────────────────────────────────
#
# The events API never carries a clock time: `startDate` is local midnight on
# every listing, so without help every Ann Arbor event reads as all-day ("The
# Verve Pipe" at The Ark is 8:00 PM; the digest showed no time at all). The
# event's own page does carry it, in the script that draws the page:
#
#     var startDate = "Saturday, November 14, 2026 8:00 PM";
#     var time = "4:00 PM to 6:00 PM";
#
# A plain `requests` fetch of that page is answered (it is the calendar's API
# that insists on the browser), so one GET per distinct event fills it in.

_DETAIL_PREFIX = SITE_ROOT + "/event/"
_DETAIL_WORKERS = 6
_DETAIL_TIMEOUT = 15
_MAX_DETAIL_FETCHES = 120

_JS_VAR_RE = r'var\s+{name}\s*=\s*"([^"]*)"'
_CLOCK_RE = re.compile(
    r"\b(noon|midnight|(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\b\.?)",
    re.IGNORECASE,
)


def _js_var(html: str, name: str) -> str:
    m = re.search(_JS_VAR_RE.format(name=re.escape(name)), html)
    return m.group(1).strip() if m else ""


def _clock_times(text: str) -> list[time]:
    """Every clock time written in `text`, in order ("4:00 PM to 6 p.m.")."""
    found = []
    for m in _CLOCK_RE.finditer(text or ""):
        word = m.group(1).lower()
        if word == "noon":
            found.append(time(12, 0))
            continue
        if word == "midnight":
            found.append(time(0, 0))
            continue
        hour, minute = int(m.group(2)), int(m.group(3) or 0)
        if not (1 <= hour <= 12 and minute < 60):
            continue
        hour = hour % 12 + (12 if m.group(4).lower() == "p" else 0)
        found.append(time(hour, minute))
    return found


def _detail_times(html: str) -> tuple[time | None, time | None]:
    """Start and end clock times from an event page, or (None, None).

    `time` is the page's own summary ("8:00 PM", "4:00 PM to 6:00 PM"); when
    it is empty, `startDate`/`endDate` can still carry a time after the date.
    Only the clock time is kept: for a recurring event the page shows its next
    occurrence, not necessarily the one being listed.
    """
    times = _clock_times(_js_var(html, "time"))
    if times:
        return times[0], (times[1] if len(times) > 1 else None)
    start = _clock_times(_js_var(html, "startDate"))
    if not start:
        return None, None
    end = _clock_times(_js_var(html, "endDate"))
    return start[0], (end[0] if end else None)


def _fetch_detail(session, url: str) -> str:
    resp = session.get(url, timeout=_DETAIL_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _add_times(events: list[dict], session=None) -> tuple[int, list[str]]:
    """Fill in start (and end) times for all-day events from their pages.

    Changes `events` in place. Returns how many got a time, and the fetches
    that failed. An event whose page names no time stays all-day.
    """
    urls = []
    for ev in events:
        url = ev.get("url") or ""
        if ev.get("all_day") and url.startswith(_DETAIL_PREFIX) and url not in urls:
            urls.append(url)
    urls = urls[:_MAX_DETAIL_FETCHES]
    if not urls:
        return 0, []

    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": eventpage.BROWSER_UA})

    def fetch(url):
        try:
            return url, _detail_times(_fetch_detail(session, url)), None
        except Exception as e:  # one bad page must not cost the others
            return url, (None, None), f"{url}: {e}"

    with ThreadPoolExecutor(max_workers=_DETAIL_WORKERS) as pool:
        results = list(pool.map(fetch, urls))

    by_url = {url: times for url, times, _ in results}
    failures = [err for _, _, err in results if err]

    timed = 0
    for ev in events:
        start_t, end_t = by_url.get(ev.get("url"), (None, None))
        if not ev.get("all_day") or start_t is None:
            continue
        day = ev["start_dt"].date()
        ev["start_dt"] = datetime.combine(day, start_t, tzinfo=LOCAL_TZ)
        if end_t is not None:
            end_dt = datetime.combine(day, end_t, tzinfo=LOCAL_TZ)
            if end_dt <= ev["start_dt"]:  # runs past midnight
                end_dt += timedelta(days=1)
            ev["end_dt"] = end_dt
        ev["all_day"] = False
        timed += 1
    return timed, failures


def _with_times(events: list[dict]) -> tuple[list[dict], str]:
    """`_add_times`, and what went wrong reading pages ("" if nothing did)."""
    try:
        _timed, failures = _add_times(events)
    except Exception as e:
        return events, f"start times not read: {e}"
    if failures:
        return events, (
            f"{len(failures)} event page(s) unreadable, left without a time "
            f"(first: {failures[0]})"
        )
    return events, ""


def _scrape_events(today: date, lookahead_days: int) -> list[dict]:
    cutoff = today + timedelta(days=lookahead_days)

    try:
        soup, payloads, widened = eventpage.render_page_capturing(
            BASE_URL,
            capture_pattern=_API_PATTERN,
            wait_selectors=WAIT_SELECTORS,
            evaluate=_PAGE_THROUGH_JS.strip(),
            evaluate_args=lambda captured: [
                _first_query(captured),
                f"{cutoff.isoformat()}T23:59:59.000Z",
                _MAX_EXTRA_PAGES,
            ],
        )
    except Exception as e:
        errors.record("annarbor", f"could not render annarbor.org/events: {e}")
        return []

    # The page's own call only asks for its first screen; the widened one
    # covers the digest window. Both are the same API, so merge and dedupe.
    widened = widened if isinstance(widened, dict) else {}
    docs = _docs_of(widened.get("data"))
    from_page = [
        d for p in payloads
        for d in _docs_of(p.get("json") if isinstance(p, dict) and "json" in p else p)
    ]
    if docs:
        events, untimed = _with_times(_events_from_docs(docs + from_page))
        stopped = widened.get("error")
        errors.note_strategy(
            "annarbor",
            f"annarbor.org: {len(events)} events from the events API"
            + (f" — paging stopped at {stopped}" if stopped else "")
            + (f" — {untimed}" if untimed else ""),
            degraded=bool(stopped or untimed),
        )
        return events

    if from_page:
        events, untimed = _with_times(_events_from_docs(from_page))
        why = widened.get("error") or "no further screens"
        errors.note_strategy(
            "annarbor",
            f"annarbor.org: {len(events)} events from the calendar's first screen only "
            f"— paging got {why}" + (f" — {untimed}" if untimed else ""),
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
                all_day=raw.get("all_day", False),
                location=raw.get("location") or DEFAULT_LOCATION,
                source="annarbor",
                url=raw.get("url") or BASE_URL,
                tags=["local", "ann-arbor"],
            ))

        return eventpage.within_window(events, today, lookahead_days, LOCAL_TZ, "annarbor")
