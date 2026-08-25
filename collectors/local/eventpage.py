"""
Layered event extraction shared by the local calendar scrapers.

Each of these scrapers used to bind to exactly one CMS's class names —
`article.type-tribe_events` for the two visitor bureaus, `div.event` for
downtown Tecumseh. That works right up until the site re-themes or moves its
calendar behind JavaScript, and then the scraper fetches a perfectly good page,
matches nothing, and the section quietly disappears.

The fix is to stop depending on any single layout. A calendar page almost
always carries its events in more than one form:

  1. The Events Calendar REST API (WordPress) — stable JSON, survives themes
  2. schema.org JSON-LD — emitted for SEO by most event CMSs, theme-independent
  3. schema.org microdata — the older inline form of the same data
  4. CSS selectors over the rendered markup — the last resort, and the only
     one that breaks when a designer touches the page

Callers try them in that order and report which one produced the events, so a
silent downgrade (REST gone, now scraping HTML) is visible before the HTML
layer breaks too.
"""

import html
import json
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# schema.org event types that do not end in "Event" and so need naming.
_EXTRA_EVENT_TYPES = {"Festival", "Hackathon", "CourseInstance"}

# Wrapper types worth descending into when hunting for events.
_JSONLD_CONTAINER_KEYS = ("@graph", "itemListElement", "subEvent", "event", "events")

_WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(str(text or ""))).strip()


def _is_event_type(raw_type) -> bool:
    types = raw_type if isinstance(raw_type, list) else [raw_type]
    for t in types:
        if not isinstance(t, str):
            continue
        name = t.rsplit("/", 1)[-1]
        if name.endswith("Event") or name in _EXTRA_EVENT_TYPES:
            return True
    return False


def _to_local(value, tz) -> datetime | None:
    """Parse a date/datetime string and pin it to the calendar's local zone.

    Date-only values ("2026-08-20") become local midnight, which is how an
    all-day listing should sort against timed ones.
    """
    if not value:
        return None
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = dateparser.parse(text)
    except Exception:
        return None
    if parsed is None:
        return None
    if isinstance(parsed, datetime) and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    elif not isinstance(parsed, datetime):
        parsed = datetime.combine(parsed, time.min, tzinfo=tz)
    return parsed


def _place_name(value) -> str:
    """Flatten a schema.org location (string, Place, or list of either)."""
    if not value:
        return ""
    if isinstance(value, list):
        for item in value:
            name = _place_name(item)
            if name:
                return name
        return ""
    if isinstance(value, str):
        return _clean(value)
    if not isinstance(value, dict):
        return ""

    name = _clean(value.get("name", ""))
    address = value.get("address")
    if isinstance(address, dict):
        parts = [
            _clean(address.get("streetAddress", "")),
            _clean(address.get("addressLocality", "")),
            _clean(address.get("addressRegion", "")),
        ]
        addr_str = ", ".join(p for p in parts if p)
    else:
        addr_str = _clean(address) if address else ""

    if name and addr_str and name not in addr_str:
        return f"{name}, {addr_str}"
    return name or addr_str


def _first_url(value) -> str:
    if isinstance(value, list):
        for item in value:
            url = _first_url(item)
            if url:
                return url
        return ""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, dict):
        return _first_url(value.get("url") or value.get("@id") or "")
    return ""


# ── Layer 2: JSON-LD ─────────────────────────────────────────────────────────

def _walk_jsonld(node, found: list[dict], depth: int = 0) -> None:
    """Collect Event-typed dicts from arbitrarily nested JSON-LD.

    Sites wrap events in @graph, in an ItemList, or inside a WebPage's
    mainEntity; descending blindly is cheaper than guessing which.
    """
    if depth > 8:
        return
    if isinstance(node, list):
        for item in node:
            _walk_jsonld(item, found, depth + 1)
        return
    if not isinstance(node, dict):
        return

    if _is_event_type(node.get("@type")) and node.get("name"):
        found.append(node)
        # An event's subEvents are separate listings; keep descending.

    for key in _JSONLD_CONTAINER_KEYS:
        if key in node:
            _walk_jsonld(node[key], found, depth + 1)
    if "mainEntity" in node:
        _walk_jsonld(node["mainEntity"], found, depth + 1)
    if "item" in node:
        _walk_jsonld(node["item"], found, depth + 1)


def parse_jsonld(soup: BeautifulSoup, tz, default_location: str = "") -> list[dict]:
    """Extract events from schema.org JSON-LD blocks."""
    found: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        _walk_jsonld(data, found)

    events = []
    seen = set()
    for item in found:
        start = _to_local(item.get("startDate"), tz)
        if not start:
            continue
        title = _clean(item.get("name", ""))
        if not title:
            continue
        url = _first_url(item.get("url"))
        key = (title, start)
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "title": title,
            "start_dt": start,
            "end_dt": _to_local(item.get("endDate"), tz),
            "location": _place_name(item.get("location")) or default_location,
            "url": url,
        })
    return events


# ── Layer 3: microdata ───────────────────────────────────────────────────────

def _itemprop_value(scope, name: str) -> str:
    """Read an itemprop, preferring machine-readable attributes over text."""
    el = scope.find(attrs={"itemprop": name})
    if el is None:
        return ""
    for attr in ("content", "datetime", "href"):
        value = el.get(attr)
        if value:
            return _clean(value)
    return _clean(el.get_text(" ", strip=True))


def parse_microdata(soup: BeautifulSoup, tz, default_location: str = "") -> list[dict]:
    """Extract events from inline schema.org microdata."""
    events = []
    for scope in soup.select("[itemtype]"):
        itemtype = scope.get("itemtype", "")
        if isinstance(itemtype, list):
            itemtype = " ".join(itemtype)
        if "schema.org" not in itemtype or not _is_event_type(itemtype):
            continue

        title = _itemprop_value(scope, "name")
        start = _to_local(_itemprop_value(scope, "startDate"), tz)
        if not title or not start:
            continue

        events.append({
            "title": title,
            "start_dt": start,
            "end_dt": _to_local(_itemprop_value(scope, "endDate"), tz),
            "location": _itemprop_value(scope, "location") or default_location,
            "url": _itemprop_value(scope, "url"),
        })
    return events


# ── Layer 4: CSS selectors ───────────────────────────────────────────────────

# Ordered widest-known-good first. Kept deliberately long: each entry is a
# layout some version of these calendars actually shipped, and matching an
# obsolete one costs nothing while missing the current one costs the section.
DEFAULT_ITEM_SELECTORS = (
    "article.type-tribe_events",
    ".tribe-events-calendar-list__event",
    ".tribe-events-loop .tribe-event-list-item",
    ".tribe-event-list-item",
    ".tribe-events-loop article",
    ".tribe-events-calendar article",
    "[class*='tribe-events'] article",
    "article[class*='event']",
    "li[class*='event-item']",
    "div[class*='event-item']",
    "div[class*='eventItem']",
)

_TITLE_SELECTORS = (
    "h2 a", "h3 a", "h4 a", ".tribe-event-url",
    "[class*='title'] a", "a[class*='title']",
    "h2", "h3", "h4", "[class*='title']",
)

_DATE_SELECTORS = (
    "time[datetime]", "[datetime]", "abbr[title]",
    ".tribe-event-schedule-details", ".tribe-events-schedule",
    "[class*='date']", "[class*='schedule']", "[class*='when']",
)

_LOCATION_SELECTORS = (
    ".tribe-venue", ".tribe-events-venue-details",
    "[class*='venue']", "[class*='location']",
)


def _select_first(item, selectors):
    for selector in selectors:
        el = item.select_one(selector)
        if el is not None:
            return el
    return None


def parse_selectors(
    soup: BeautifulSoup,
    tz,
    default_location: str = "",
    item_selectors=DEFAULT_ITEM_SELECTORS,
    base_url: str = "",
) -> list[dict]:
    """Extract events by walking repeated listing markup."""
    items = []
    for selector in item_selectors:
        items = soup.select(selector)
        if items:
            break
    if not items:
        return []

    events = []
    for item in items:
        title_el = _select_first(item, _TITLE_SELECTORS)
        if title_el is None:
            continue
        title = _clean(title_el.get_text(" ", strip=True))
        if not title:
            continue

        url = title_el.get("href") or ""
        if not url:
            link = item.find("a", href=True)
            url = link["href"] if link else ""
        url = _absolute(url, base_url)

        start = None
        for date_el in _iter_date_elements(item):
            raw = (
                date_el.get("datetime")
                or date_el.get("content")
                or date_el.get("title")
                or date_el.get_text(" ", strip=True)
            )
            start = _to_local_fuzzy(raw, tz)
            if start:
                break
        if not start:
            continue

        loc_el = _select_first(item, _LOCATION_SELECTORS)
        events.append({
            "title": title,
            "start_dt": start,
            "end_dt": None,
            "location": _clean(loc_el.get_text(" ", strip=True)) if loc_el else default_location,
            "url": url or base_url,
        })
    return events


def _iter_date_elements(item):
    for selector in _DATE_SELECTORS:
        for el in item.select(selector):
            yield el


def _to_local_fuzzy(value, tz) -> datetime | None:
    """Parse a human date string ("Thu, August 20 @ 7:00 pm") if a strict parse fails."""
    parsed = _to_local(value, tz)
    if parsed:
        return parsed
    text = _clean(value)
    if not text:
        return None
    try:
        # fuzzy picks the date out of surrounding words; a default year keeps
        # bare "August 20" from landing in whatever year dateutil assumes.
        naive = dateparser.parse(text, fuzzy=True, default=datetime.combine(date.today(), time.min))
    except Exception:
        return None
    if naive is None:
        return None
    return naive.replace(tzinfo=tz) if naive.tzinfo is None else naive


def _absolute(url: str, base_url: str) -> str:
    if not url or not base_url:
        return url
    if url.startswith(("http://", "https://")):
        return url
    parts = urlsplit(base_url)
    if url.startswith("//"):
        return f"{parts.scheme}:{url}"
    if url.startswith("/"):
        return urlunsplit((parts.scheme, parts.netloc, url, "", ""))
    return base_url.rstrip("/") + "/" + url


# ── Layer 1: The Events Calendar REST API ────────────────────────────────────

def fetch_tribe_rest(session, page_url: str, start: date, end: date, tz, timeout: int = 15):
    """Query the WordPress Events Calendar REST API for a date range.

    Returns a list of events, or None when the endpoint is not there — the
    caller needs that difference to tell "not a Tribe site" from "a Tribe site
    with a genuinely quiet week".
    """
    parts = urlsplit(page_url)
    endpoint = urlunsplit((parts.scheme, parts.netloc, "/wp-json/tribe/events/v1/events", "", ""))
    params = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "per_page": 50,
    }
    try:
        resp = session.get(endpoint, params=params, timeout=timeout)
        payload = resp.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    # The API answers 404 with a "no events in this range" code, which is a
    # working endpoint reporting an empty week — not a missing endpoint.
    if "events" not in payload:
        code = str(payload.get("code", ""))
        if "no-events" in code or "no_events" in code:
            return []
        return None

    events = []
    for item in payload.get("events") or []:
        if not isinstance(item, dict):
            continue
        start_dt = _to_local(item.get("start_date") or item.get("utc_start_date"), tz)
        title = _clean(item.get("title", ""))
        if not start_dt or not title:
            continue
        venue = item.get("venue") or {}
        location = ""
        if isinstance(venue, dict):
            location = _clean(venue.get("venue") or venue.get("address") or "")
        events.append({
            "title": title,
            "start_dt": start_dt,
            "end_dt": _to_local(item.get("end_date"), tz),
            "location": location,
            "url": _clean(item.get("url", "")),
        })
    return events


# ── Rendering ────────────────────────────────────────────────────────────────

# Chromium's headless default announces itself — "HeadlessChrome/…" in the
# User-Agent, navigator.webdriver set — and Cloudflare answers that with a 403
# interstitial. Presenting a stock desktop Chrome is the difference between the
# events page and 400 characters of "Just a moment...".
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

_CHALLENGE_TITLES = (
    "just a moment",
    "attention required",
    "checking your browser",
    "access denied",
    "please wait",
)


def _is_challenge_title(title: str) -> bool:
    lowered = (title or "").lower()
    return any(marker in lowered for marker in _CHALLENGE_TITLES)


def new_browser_page(pw, **context_kwargs):
    """Open a page that looks like somebody's desktop Chrome, not a crawler."""
    browser = pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=BROWSER_UA,
        locale="en-US",
        viewport={"width": 1280, "height": 1800},
        **context_kwargs,
    )
    return browser, context.new_page()


def _settle_challenge(page, budget_ms: int = 20_000) -> None:
    """Wait out a bot interstitial, which swaps itself for the real page.

    Reading the DOM while the challenge is still up is how the downtown
    calendar came back as a 392-character page with no events on it.
    """
    remaining = budget_ms
    while remaining > 0:
        try:
            title = page.title()
        except Exception:
            title = ""
        if not _is_challenge_title(title):
            return
        page.wait_for_timeout(2_000)
        remaining -= 2_000


def render_page(url: str, wait_selectors=(), settle_ms: int = 2500, timeout_ms: int = 30_000):
    """Render a page in a browser-shaped Chromium and return the parsed DOM.

    `domcontentloaded` alone fires before a client-rendered calendar has drawn
    anything, which is exactly how a JS-only page looks like an empty one. Wait
    for a content selector when we have one, and fall back to a settle delay.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as pw:
        browser, page = new_browser_page(pw)
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            _settle_challenge(page)

            drew_content = False
            for selector in wait_selectors:
                try:
                    page.wait_for_selector(selector, timeout=6_000)
                    drew_content = True
                    break
                except PWTimeout:
                    continue

            if not drew_content:
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except PWTimeout:
                    pass
                page.wait_for_timeout(settle_ms)

            html_text = page.content()
        finally:
            browser.close()

    return BeautifulSoup(html_text, "html.parser")


# ── Orchestration ────────────────────────────────────────────────────────────

def scrape_calendar(
    source: str,
    session,
    page_url: str,
    today: date,
    lookahead_days: int,
    tz,
    default_location: str = "",
    site_label: str = "",
    item_selectors=DEFAULT_ITEM_SELECTORS,
    wait_selectors=(),
    allow_render: bool = True,
) -> list[dict]:
    """Run every extraction layer against a calendar page until one produces events.

    Reports through `errors` so the digest can say which layer worked, or —
    when they all came up empty — what the page actually looked like.
    """
    import errors

    cutoff = today + timedelta(days=lookahead_days)
    label = site_label or urlsplit(page_url).netloc
    tried: list[str] = []

    rest = fetch_tribe_rest(session, page_url, today, cutoff, tz)
    if rest:
        errors.note_strategy(source, f"{label}: {len(rest)} events from the Events Calendar REST API")
        return rest
    tried.append("REST endpoint absent" if rest is None else "REST returned 0 events")

    url = f"{page_url}?startdate={today:%Y-%m-%d}&enddate={cutoff:%Y-%m-%d}"

    static_soup = None
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        static_soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        errors.record(source, f"could not fetch {url}: {e}")

    if static_soup is not None:
        events, how = extract_all(static_soup, tz, default_location, item_selectors, page_url)
        if events:
            errors.note_strategy(
                source,
                f"{label}: {len(events)} events from {how} (static HTML)",
                degraded=(how == "CSS selectors"),
            )
            return events
        tried.append("static HTML matched nothing")

    if allow_render:
        try:
            rendered = render_page(url, wait_selectors=wait_selectors)
        except Exception as e:
            errors.record(source, f"could not render {label}: {e}")
            rendered = None

        if rendered is not None:
            events, how = extract_all(rendered, tz, default_location, item_selectors, page_url)
            if events:
                # Falling back this far is a working scrape, but a slower and
                # more fragile one than the site used to need. Say so.
                errors.note_strategy(
                    source,
                    f"{label}: {len(events)} events from {how} — only after browser rendering",
                    degraded=True,
                )
                return events
            tried.append("rendered HTML matched nothing")
            static_soup = rendered

    if static_soup is None:
        # Nothing ever loaded, and every attempt is already on the failure
        # list. "Fetched but parsed nothing" would be the wrong diagnosis.
        return []

    errors.note_suspect(
        source,
        f"{label}: no events from any strategy ({'; '.join(tried)}) — {describe_page(static_soup)}",
    )
    return []


def within_window(events, today: date, lookahead_days: int, tz, source: str):
    """Keep events inside the digest window, and explain a wholesale drop.

    Parsing a calendar perfectly and then filtering every event out is the
    other way a source reaches zero. A venue that books months ahead — the
    theatre especially — hits this every quiet week, and it used to land in the
    same undifferentiated "No events returned" list as a broken scraper.
    """
    import errors

    cutoff = today + timedelta(days=lookahead_days)
    kept = []
    next_up = None

    for event in events:
        start_date = event.start.astimezone(tz).date()
        end_date = event.end.astimezone(tz).date() if event.end else start_date
        if end_date < today - timedelta(days=1):
            continue
        if start_date > cutoff:
            if next_up is None or start_date < next_up:
                next_up = start_date
            continue
        kept.append(event)

    if events and not kept:
        if next_up:
            errors.note_idle(
                source,
                f"parsed {len(events)} events fine, none within {lookahead_days} days "
                f"— next one is {next_up:%b %-d}",
            )
        else:
            errors.note_idle(
                source, f"parsed {len(events)} events fine, all of them already past"
            )

    return kept


def extract_all(soup, tz, default_location, item_selectors, base_url) -> tuple[list[dict], str]:
    """Try the page-parsing layers in order of durability."""
    for name, parse in (
        ("JSON-LD", lambda: parse_jsonld(soup, tz, default_location)),
        ("microdata", lambda: parse_microdata(soup, tz, default_location)),
        ("CSS selectors", lambda: parse_selectors(soup, tz, default_location, item_selectors, base_url)),
    ):
        try:
            events = parse()
        except Exception:
            continue
        if events:
            return events, name
    return [], ""


# ── Diagnostics ──────────────────────────────────────────────────────────────

def describe_page(soup: BeautifulSoup) -> str:
    """One-line description of what a page looked like, for a failure note.

    A scraper that matched nothing should say what it was staring at: a bot
    wall, an empty SPA shell and a re-themed calendar all need different fixes,
    and the character count alone does not tell them apart.
    """
    title = _clean(soup.title.get_text()) if soup.title else ""
    text_len = len(soup.get_text(strip=True))
    scripts = len(soup.find_all("script"))
    bits = [f"title={title[:60]!r}" if title else "no <title>", f"{text_len} chars text"]

    roots = soup.select("#root, #app, [data-reactroot], [ng-app], [data-vue-app]")
    if roots and text_len < 4000:
        bits.append(f"empty SPA root behind {scripts} scripts — needs rendering")
    elif text_len < 1200:
        bits.append("near-empty body — likely a bot wall or redirect")

    lowered = title.lower()
    if any(w in lowered for w in ("just a moment", "attention required", "access denied", "blocked")):
        bits.append("bot-challenge page")

    return ", ".join(bits)
