#!/usr/bin/env python3
"""
Diagnostic: what do the four local calendars actually serve?

All four local sources went empty at once, each in a different way:

  tecumseh  — "Just a moment...", 392 chars  (Cloudflare interstitial)
  adrian    — real page, 3200 chars, no events matched by any layer
  annarbor  — real page, 4999 chars, no events matched by any layer
  tca       — the VBO iframe never appeared within 15s

Guessing new selectors from those one-line summaries is how the last three
rounds of this went. This script runs from a GitHub Actions runner — which,
unlike a dev box behind a proxy, can actually reach the sites — and prints
what each page is: which endpoints answer, what structured data is on it,
which repeated markup carries dates, and which frames exist.

Run: python scripts/local_probe.py [tecumseh|adrian|annarbor|tca] ...
"""

import json
import os
import re
import sys
from collections import Counter
from datetime import date, timedelta

# Run from the repo root's point of view, so `collectors` and `errors` import
# the same way they do for main.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from bs4 import BeautifulSoup

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

SITES = {
    "tecumseh": "https://www.downtowntecumseh.com/events/",
    "adrian": "https://www.visitlenawee.com/events/",
    "annarbor": "https://www.visitannarbor.org/events/",
}

TCA_URL = "https://tecumsehcenterforthearts.vbotickets.com/events"

# Endpoints worth asking for by name: each is a whole extraction layer that
# would beat scraping markup if it answers.
CANDIDATE_PATHS = (
    "/wp-json/tribe/events/v1/events",
    "/wp-json/wp/v2/tribe_events",
    "/wp-json/wp/v2/types",
    "/events/feed/",
    "/events/?ical=1",
    "/api/events",
    "/sitemap.xml",
)

# Substrings that identify the calendar platform from the page source alone.
PLATFORM_MARKERS = (
    "rest_v2",              # Simpleview DMS — JSON events API
    "plugins_events",       # Simpleview events plugin
    "tribe-events",         # The Events Calendar
    "wp-json",
    "__NEXT_DATA__",
    "__NUXT__",
    "window.__INITIAL",
    "localist",
    "tockify",
    "eventbrite",
    "squarespace",
    "elfsight",
    "growthzone",
    "chambermaster",
    "simpleview",
)

_DATE_TEXT_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    re.IGNORECASE,
)


def head(label: str) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")


def show_response(label: str, resp) -> None:
    body = (resp.text or "")[:300].replace("\n", " ")
    print(f"  {label}: {resp.status_code} {resp.headers.get('content-type','?')[:40]} "
          f"{len(resp.content)}B")
    if resp.status_code < 400 or resp.status_code == 404:
        print(f"    body[:300]: {body}")


def probe_endpoints(session, base: str) -> None:
    origin = "/".join(base.split("/")[:3])
    today = date.today()
    for path in CANDIDATE_PATHS:
        url = origin + path
        params = {}
        if "tribe/events" in path:
            params = {
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=7)).isoformat(),
                "per_page": 5,
            }
        try:
            resp = session.get(url, params=params, timeout=20)
        except Exception as e:
            print(f"  {path}: ERROR {type(e).__name__}: {e}")
            continue
        show_response(path, resp)


def describe_markup(soup: BeautifulSoup, source_html: str) -> None:
    title = soup.title.get_text(strip=True) if soup.title else "(no title)"
    text = soup.get_text(" ", strip=True)
    print(f"  title: {title!r}")
    print(f"  text chars: {len(text)}  scripts: {len(soup.find_all('script'))}  "
          f"html bytes: {len(source_html)}")

    # Layer 1/2 evidence: structured data.
    blocks = soup.find_all("script", type="application/ld+json")
    types = []
    for block in blocks:
        try:
            data = json.loads(block.string or block.get_text() or "{}")
        except Exception:
            types.append("(unparseable)")
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@type" in node:
                    types.append(str(node["@type"]))
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    print(f"  ld+json blocks: {len(blocks)}  types: {sorted(set(types))[:12]}")
    print(f"  microdata itemtypes: "
          f"{sorted({e.get('itemtype','') for e in soup.select('[itemtype]')})[:8]}")

    # Which platform is this?
    hits = [m for m in PLATFORM_MARKERS if m.lower() in source_html.lower()]
    print(f"  platform markers: {hits}")
    for marker in ("rest_v2", "__NEXT_DATA__", "window.__INITIAL"):
        idx = source_html.lower().find(marker.lower())
        if idx != -1:
            print(f"    {marker} context: ...{source_html[max(0, idx - 120): idx + 220]}...")

    # Which repeated markup carries dates? This is what a selector layer needs.
    date_classes = Counter()
    for el in soup.find_all(["article", "li", "div", "section", "a"]):
        own = el.get_text(" ", strip=True)
        if not own or len(own) > 400:
            continue
        if not _DATE_TEXT_RE.search(own):
            continue
        classes = el.get("class") or []
        if classes:
            date_classes[" ".join(classes)[:80]] += 1
    print("  repeated date-bearing classes:")
    for cls, n in date_classes.most_common(12):
        print(f"    {n:3d}x  .{cls}")

    times = soup.select("time[datetime]")
    print(f"  <time datetime> elements: {len(times)}  "
          f"sample: {[t.get('datetime') for t in times[:5]]}")

    event_links = [a.get("href") for a in soup.find_all("a", href=True)
                   if re.search(r"/event", a["href"], re.IGNORECASE)]
    print(f"  links containing '/event': {len(event_links)}  sample: {event_links[:8]}")

    frames = [(f.get("id"), f.get("name"), f.get("src")) for f in soup.find_all("iframe")]
    print(f"  iframes: {frames[:6]}")


def probe_static(name: str, url: str) -> str:
    head(f"{name} — static fetch  {url}")
    session = requests.Session()
    session.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        resp = session.get(url, timeout=25)
    except Exception as e:
        print(f"  fetch failed: {type(e).__name__}: {e}")
        return ""
    print(f"  status {resp.status_code}, final url {resp.url}")
    print(f"  server: {resp.headers.get('server')}  cf-ray: {resp.headers.get('cf-ray')}")
    describe_markup(BeautifulSoup(resp.text, "html.parser"), resp.text)

    head(f"{name} — endpoint probes")
    probe_endpoints(session, url)
    return resp.text


def probe_rendered(name: str, url: str) -> None:
    """Render with a browser-shaped Chromium and report what drew."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    head(f"{name} — rendered  {url}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=BROWSER_UA,
            locale="en-US",
            viewport={"width": 1280, "height": 1800},
        )
        page = context.new_page()
        try:
            resp = page.goto(url, timeout=45_000, wait_until="domcontentloaded")
            print(f"  goto status: {resp.status if resp else '(none)'}")
        except PWTimeout as e:
            print(f"  goto timed out: {e}")
            browser.close()
            return

        print(f"  title right after load: {page.title()!r}")
        # A Cloudflare interstitial replaces itself once its JS finishes; the
        # question is whether it ever does for a headless runner.
        for waited in range(1, 7):
            page.wait_for_timeout(5_000)
            title = page.title()
            print(f"  +{waited * 5}s title: {title!r}  chars: {len(page.inner_text('body'))}")
            if "just a moment" not in title.lower() and "attention required" not in title.lower():
                break

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            print("  (networkidle never reached)")

        print(f"  final url: {page.url}")
        print(f"  frames: {[(f.name, f.url[:110]) for f in page.frames]}")
        html_text = page.content()
        browser.close()

    describe_markup(BeautifulSoup(html_text, "html.parser"), html_text)


def probe_tca() -> None:
    """The TCA widget: does the iframe exist, and can we address it directly?"""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    head(f"tca — rendered  {TCA_URL}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(user_agent=BROWSER_UA, locale="en-US")
        page = context.new_page()

        requests_seen: list[str] = []
        page.on("request", lambda r: requests_seen.append(f"{r.method} {r.url}"))

        try:
            resp = page.goto(TCA_URL, timeout=60_000, wait_until="domcontentloaded")
            print(f"  goto status: {resp.status if resp else '(none)'}  title: {page.title()!r}")
        except PWTimeout as e:
            print(f"  goto timed out: {e}")
            browser.close()
            return

        page.wait_for_timeout(12_000)
        print(f"  final url: {page.url}")
        print(f"  #MyEventWrapper present: {page.query_selector('#MyEventWrapper') is not None}")
        print("  frames:")
        for frame in page.frames:
            print(f"    name={frame.name!r} url={frame.url[:140]}")
        for sel in ("iframe", ".EventListWrapper", "[class*='Event']", "[class*='event']"):
            print(f"  count {sel!r}: {len(page.query_selector_all(sel))}")

        html_text = page.content()
        print(f"  page html bytes: {len(html_text)}")
        print(f"  body text[:600]: {page.inner_text('body')[:600]!r}")

        interesting = [r for r in requests_seen
                       if re.search(r"vbotickets|event|api|json", r, re.IGNORECASE)]
        print(f"  network requests ({len(requests_seen)} total), interesting:")
        for line in interesting[:35]:
            print(f"    {line[:160]}")

        # Whatever the widget fetches is a better target than the wrapper page.
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                frame_html = frame.content()
            except Exception as e:
                print(f"  frame {frame.url[:80]} content failed: {e}")
                continue
            print(f"\n  --- frame {frame.url[:140]} ---")
            describe_markup(BeautifulSoup(frame_html, "html.parser"), frame_html)

        browser.close()

    head("tca — static fetch of the wrapper and the plugin host")
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    for url in (TCA_URL, "https://www.thetca.org/tickets.html"):
        try:
            resp = session.get(url, timeout=25)
        except Exception as e:
            print(f"  {url}: ERROR {type(e).__name__}: {e}")
            continue
        print(f"\n  {url} -> {resp.status_code}, {len(resp.content)}B")
        soup = BeautifulSoup(resp.text, "html.parser")
        print(f"    iframes: {[(f.get('id'), f.get('name'), f.get('src')) for f in soup.find_all('iframe')]}")
        for script in soup.find_all("script"):
            body = script.string or ""
            if "vbotickets" in body.lower() or "MyEvent" in body:
                print(f"    inline script snippet: {' '.join(body.split())[:400]}")


# ── Round 2: capture what the widgets actually fetch ─────────────────────────

def _capture_xhr(url: str, match: str, label: str, wait_ms: int = 15_000) -> None:
    """Render a page and print the API calls it makes, with their responses.

    Guessing a JSON API's query shape is how the last fix went wrong. The page
    already knows the shape — watch it ask.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    head(f"{label} — XHR capture  {url}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(user_agent=BROWSER_UA, locale="en-US",
                                      viewport={"width": 1280, "height": 2000})
        page = context.new_page()

        hits: list[tuple[str, str]] = []

        def on_response(resp):
            if not re.search(match, resp.url, re.IGNORECASE):
                return
            try:
                body = resp.text()
            except Exception as e:
                body = f"(body unavailable: {e})"
            hits.append((f"{resp.status} {resp.url}", body))

        page.on("response", on_response)

        try:
            page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        except PWTimeout as e:
            print(f"  goto timed out: {e}")
            browser.close()
            return
        page.wait_for_timeout(wait_ms)
        print(f"  final url: {page.url}  title: {page.title()!r}")
        browser.close()

    print(f"  matching responses: {len(hits)}")
    for line, body in hits[:6]:
        print(f"\n  --- {line[:400]}")
        print(f"      body[:2500]: {body[:2500]}")


def probe_simpleview_token() -> None:
    """How does annarbor.org build its events query, and where is the token?"""
    url = "https://www.annarbor.org/events/"
    head(f"annarbor — Simpleview query construction  {url}")
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    html_text = session.get(url, timeout=25).text

    for needle in ("simpleToken", "rest_v2"):
        for m in list(re.finditer(needle, html_text))[:3]:
            lo, hi = max(0, m.start() - 1500), m.start() + 1500
            print(f"\n  --- {needle} @ {m.start()} ---")
            print("  " + " ".join(html_text[lo:hi].split())[:2800])


def probe_vbo_fragment() -> None:
    """Can the VBO event list be fetched without a browser at all?"""
    site = "d7b7befb-80ac-4c77-b28c-a23b353a5df7"
    urls = [
        f"https://plugin.vbotickets.com/Plugin/events/showevents?ViewType=list&EventType=current&day=&s={site}",
        f"https://plugin.vbotickets.com/plugin/events?s={site}",
    ]
    head("tca — plain fetch of the VBO widget endpoints")
    session = requests.Session()
    session.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,*/*;q=0.8",
        "Referer": "https://tecumsehcenterforthearts.vbotickets.com/",
        "X-Requested-With": "XMLHttpRequest",
    })
    for url in urls:
        try:
            resp = session.get(url, timeout=25)
        except Exception as e:
            print(f"  {url}: ERROR {type(e).__name__}: {e}")
            continue
        print(f"\n  {url}\n    -> {resp.status_code} {len(resp.content)}B "
              f"{resp.headers.get('content-type','?')[:40]}")
        soup = BeautifulSoup(resp.text, "html.parser")
        for sel in (".EventListWrapper", ".EventListExtra", ".TextEventDate",
                    "h2.HeaderEventName", ".TextVenueName", "[class*='EventList']"):
            print(f"    {sel}: {len(soup.select(sel))}")
        card = soup.select_one(".EventListWrapper") or soup.select_one("[class*='EventList']")
        if card:
            print(f"    first card html[:1500]: {' '.join(str(card).split())[:1500]}")
        else:
            print(f"    body text[:600]: {' '.join(soup.get_text(' ', strip=True).split())[:600]}")


def probe_tecumseh_markup() -> None:
    """With a browser-shaped context, does div.event come back intact?"""
    from playwright.sync_api import sync_playwright

    url = SITES["tecumseh"]
    head(f"tecumseh — browser-shaped render, existing selectors  {url}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(user_agent=BROWSER_UA, locale="en-US")
        page = context.new_page()
        page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        page.wait_for_timeout(6_000)
        html_text = page.content()
        browser.close()

    soup = BeautifulSoup(html_text, "html.parser")
    divs = soup.find_all("div", class_="event")
    print(f"  title: {soup.title.get_text(strip=True) if soup.title else None!r}")
    print(f"  div.event count: {len(divs)}")
    for div in divs[:3]:
        print(f"\n  --- card ---\n  {' '.join(str(div).split())[:1200]}")

# ── Round 3: the exact query, and the Yodel widget's own API ─────────────────

def probe_annarbor_query() -> None:
    """Print the events query the page sends, fully decoded, plus its token."""
    from playwright.sync_api import sync_playwright
    from urllib.parse import parse_qs, urlsplit

    url = "https://www.annarbor.org/events/"
    head(f"annarbor — the exact events query  {url}")

    captured: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        page = browser.new_context(user_agent=BROWSER_UA, locale="en-US").new_page()
        page.on("request", lambda r: captured.append(r.url)
                if "events_by_date" in r.url else None)
        page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        page.wait_for_timeout(12_000)
        browser.close()

    print(f"  events_by_date requests: {len(captured)}")
    for raw in captured[:2]:
        params = parse_qs(urlsplit(raw).query)
        token = (params.get("token") or [""])[0]
        print(f"\n  token param: {token[:80]!r} (len {len(token)})")
        try:
            payload = json.loads((params.get("json") or ["{}"])[0])
        except Exception as e:
            print(f"  json param unparseable: {e}")
            continue
        print("  filter:")
        print("    " + json.dumps(payload.get("filter"), indent=2)[:3000].replace("\n", "\n    "))
        print("  options:")
        print("    " + json.dumps(payload.get("options"), indent=2)[:2500].replace("\n", "\n    "))

    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    html_text = session.get(url, timeout=25).text
    print("\n  simpleToken declarations in the page source:")
    for m in re.finditer(r"simpleToken[\"']?\s*[:=]\s*[\"']([^\"']{4,200})[\"']", html_text):
        print(f"    {m.group(0)[:220]}")
    for m in list(re.finditer(r"simpleToken", html_text))[:6]:
        print(f"    @{m.start()}: ...{' '.join(html_text[m.start()-90:m.start()+90].split())}...")


def probe_yodel_api() -> None:
    """The Lenawee calendar is a Yodel widget — what does it fetch, and draw?"""
    from playwright.sync_api import sync_playwright

    url = "https://events.yodel.today/y/widget/699331672d0ab3b826bf79e5"
    head(f"adrian — Yodel widget XHR + rendered cards  {url}")

    hits: list[tuple[str, str]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        page = browser.new_context(user_agent=BROWSER_UA, locale="en-US",
                                   viewport={"width": 1400, "height": 2200}).new_page()

        def on_response(resp):
            # Only the data calls: the widget is a Next.js app, so its own
            # hostname matches "event" on every stylesheet it loads.
            if resp.request.resource_type not in ("xhr", "fetch"):
                return
            try:
                body = resp.text()
            except Exception as e:
                body = f"(unavailable: {e})"
            hits.append((f"{resp.status} {resp.request.method} {resp.url}", body))

        page.on("response", on_response)
        page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        page.wait_for_timeout(15_000)
        html_text = page.content()
        browser.close()

    print(f"  xhr/fetch responses: {len(hits)}")
    for line, body in hits[:12]:
        print(f"\n  --- {line[:300]}")
        print(f"      body[:1800]: {' '.join(body.split())[:1800]}")

    soup = BeautifulSoup(html_text, "html.parser")
    print(f"\n  rendered widget: {len(soup.get_text(strip=True))} chars text")
    describe_markup(soup, html_text)
    for selector in ("[class*='eventcardtile']", "[class*='eventCard']",
                     "[class*='eventList']", "a[href*='/event']"):
        found = soup.select(selector)
        print(f"\n  {selector}: {len(found)}")
        if found:
            print(f"    first: {' '.join(str(found[0]).split())[:1200]}")

# ── Round 4: can each replacement path run without a browser? ────────────────

def probe_yodel_static() -> None:
    """Does the Yodel widget server-render its events, JSON-LD and all?"""
    head("adrian — Yodel widget over plain HTTP")
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA, "Accept": "text/html,*/*;q=0.8"})

    listing = session.get(SITES["adrian"], timeout=25).text
    ids = set(re.findall(r"events\.yodel\.today/y/widget/([0-9a-f]{16,32})", listing))
    print(f"  widget ids discoverable in visitlenawee.com's HTML: {sorted(ids)}")

    for widget_id in sorted(ids) or ["699331672d0ab3b826bf79e5"]:
        url = f"https://events.yodel.today/y/widget/{widget_id}"
        resp = session.get(url, timeout=25)
        print(f"\n  {url} -> {resp.status_code} {len(resp.content)}B")
        soup = BeautifulSoup(resp.text, "html.parser")
        blocks = soup.find_all("script", type="application/ld+json")
        print(f"    ld+json blocks: {len(blocks)}")
        events = parse_jsonld_preview(soup)
        print(f"    Events found by the JSON-LD layer: {len(events)}")
        for ev in events[:5]:
            print(f"      {ev}")


def parse_jsonld_preview(soup) -> list[dict]:
    """Mirror of the collector's JSON-LD layer, so the probe proves the layer."""
    out = []
    for block in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(block.string or block.get_text() or "{}")
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                types = node.get("@type")
                types = types if isinstance(types, list) else [types]
                if any(isinstance(t, str) and t.endswith("Event") for t in types) and node.get("name"):
                    loc = node.get("location")
                    if isinstance(loc, dict):
                        loc = loc.get("name")
                    out.append({
                        "name": str(node.get("name"))[:60],
                        "startDate": node.get("startDate"),
                        "endDate": node.get("endDate"),
                        "location": str(loc)[:50],
                        "url": str(node.get("url"))[:80],
                    })
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return out


def probe_annarbor_api() -> None:
    """Is the Simpleview token enforced, and does a custom date range work?"""
    head("annarbor — is the events API usable without a browser?")
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA, "Accept": "application/json,*/*"})

    endpoint = "https://www.annarbor.org/includes/rest_v2/plugins_events_events_by_date/find/"
    today = date.today()
    payload = {
        "filter": {
            "active": True,
            "date_range": {
                "start": {"$date": f"{today.isoformat()}T00:00:00.000Z"},
                "end": {"$date": f"{(today + timedelta(days=7)).isoformat()}T23:59:59.000Z"},
            },
        },
        "options": {
            "limit": 100,
            "skip": 0,
            "count": True,
            "castDocs": False,
            "fields": {
                "_id": 1, "location": 1, "date": 1, "startDate": 1, "endDate": 1,
                "recurrence": 1, "recurType": 1, "recid": 1, "title": 1, "url": 1,
                "categories": 1, "city": 1, "region": 1, "admission": 1,
                "listing.title": 1, "listing.url": 1, "listing.city": 1,
            },
            "hooks": [],
            "sort": {"date": 1, "rank": 1, "title_sort": 1},
        },
    }

    for label, token in (
        ("no token param", None),
        ("garbage token", "0" * 32),
        ("the token the page used", "2c2e93b51c6d574ef1fb9d1922b5e008"),
    ):
        params = {"json": json.dumps(payload)}
        if token is not None:
            params["token"] = token
        try:
            resp = session.get(endpoint, params=params, timeout=25)
        except Exception as e:
            print(f"  {label}: ERROR {type(e).__name__}: {e}")
            continue
        body = resp.text
        print(f"\n  {label}: {resp.status_code} {len(resp.content)}B")
        try:
            data = resp.json()
        except Exception:
            print(f"    not json: {' '.join(body.split())[:300]}")
            continue
        docs = (data.get("docs") or {})
        rows = docs.get("docs") if isinstance(docs, dict) else None
        print(f"    count={docs.get('count') if isinstance(docs, dict) else '?'} "
              f"returned={len(rows) if rows else 0}")
        for row in (rows or [])[:6]:
            print(f"      {row.get('date')} | {str(row.get('title'))[:48]} | "
                  f"{str(row.get('location'))[:30]} | {row.get('url')}")

    # Where does the token come from, if not the page HTML?
    page = session.get("https://www.annarbor.org/events/", timeout=25).text
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', page)
    print(f"\n  external scripts on the events page: {len(scripts)}")
    for src in scripts[:40]:
        if not re.search(r"core|simple|main|app|bundle", src, re.I):
            continue
        url = src if src.startswith("http") else "https://www.annarbor.org" + src
        try:
            body = session.get(url, timeout=20).text
        except Exception:
            continue
        for m in re.finditer(r"simpleToken[\"']?\s*[:=]\s*[\"']([0-9a-f]{16,64})[\"']", body):
            print(f"    token in {url[:90]}: {m.group(1)}")


def probe_vbo_loadplugin() -> None:
    """Can the widget session key be resolved over plain HTTP?"""
    head("tca — resolving the VBO widget key")
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA, "Referer": TCA_URL})
    page = session.get(TCA_URL, timeout=25).text
    site_id = re.search(r'var\s+SiteID\s*=\s*"([^"]+)"', page)
    org_id = re.search(r'var\s+OrgID\s*=\s*"([^"]+)"', page)
    print(f"  SiteID: {site_id.group(1) if site_id else None}")
    print(f"  OrgID:  {org_id.group(1) if org_id else None}")
    if not site_id:
        return

    params = {
        "siteid": site_id.group(1), "page": "ListEvents", "w": "1280", "h": "720",
        "o": org_id.group(1) if org_id else "0",
        "eid": "0", "edid": "0", "did": "0", "wlid": "0",
    }
    resp = session.get("https://plugin.vbotickets.com/plugin/loadplugin",
                       params=params, timeout=25)
    print(f"  loadplugin -> {resp.status_code} {len(resp.content)}B "
          f"{resp.headers.get('content-type','?')[:40]}")
    print(f"    body[:800]: {' '.join(resp.text.split())[:800]}")
    guids = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                       resp.text, re.I)
    print(f"    GUIDs in the response: {guids[:5]}")
    for guid in guids[:2]:
        url = (f"https://plugin.vbotickets.com/Plugin/events/showevents"
               f"?ViewType=list&EventType=current&day=&s={guid}")
        try:
            listing = session.get(url, timeout=25)
        except Exception as e:
            print(f"    {guid}: ERROR {e}")
            continue
        cards = BeautifulSoup(listing.text, "html.parser").select(".EventListWrapper")
        print(f"    key {guid} -> {listing.status_code}, {len(cards)} cards")
        for card in cards[:4]:
            title = card.select_one("h2.HeaderEventName")
            when = card.select_one(".TextEventDate")
            venue = card.select_one(".TextVenueName")
            print(f"      {when.get_text(' ', strip=True) if when else '?'} | "
                  f"{title.get_text(strip=True) if title else '?'} | "
                  f"{venue.get_text(strip=True) if venue else '?'}")

# ── Verification: run the collectors themselves ──────────────────────────────

def probe_collectors() -> None:
    """Run the four local collectors for real and print what comes back.

    Unit tests pin the parsing down against fixtures; only this says whether
    the live sites still answer the way the fixtures claim.
    """
    import errors as errmod
    from collectors.local.adrian import AdrianCollector
    from collectors.local.annarbor import AnnArborCollector
    from collectors.local.tca import TCACollector
    from collectors.local.tecumseh import TecumsehCollector

    today = date.today()
    collectors = [
        ("tecumseh", TecumsehCollector),
        ("tca", TCACollector),
        ("annarbor", AnnArborCollector),
        ("adrian", AdrianCollector),
    ]

    totals = {}
    for name, cls in collectors:
        head(f"{name} — live collect")
        try:
            events = cls({}).collect(today, 7)
        except Exception as e:
            print(f"  RAISED {type(e).__name__}: {e}")
            totals[name] = "raised"
            continue
        totals[name] = len(events)
        print(f"  {len(events)} events in the next 7 days")
        for event in events[:12]:
            print(f"    {event.start.isoformat()}  {event.title[:56]}  @ {str(event.location)[:40]}")

    head("what the digest's health block would say")
    print(json.dumps(errmod.summary(), indent=2, default=str)[:4000])

    head("summary")
    for name, count in totals.items():
        print(f"  {name}: {count}")

# ── The Tecumseh Herald half, which the downtown outage was masking ──────────

def probe_herald() -> None:
    """Why does the Herald calendar yield links but no events?"""
    from collectors.local import tecumseh

    head("tecumseh — the Herald calendar")
    today = date.today()
    for offset in (0, 1):
        month = f"{today.year + (today.month + offset - 1) // 12}-" \
                f"{(today.month + offset - 1) % 12 + 1:02d}"
        url = f"{tecumseh.HERALD_BASE}{tecumseh.HERALD_CALENDAR_PATH}/{month}"
        try:
            resp = tecumseh._session.get(url, timeout=20)
        except Exception as e:
            print(f"  {url}: ERROR {type(e).__name__}: {e}")
            continue
        print(f"\n  {url} -> {resp.status_code} {len(resp.content)}B")
        soup = BeautifulSoup(resp.text, "html.parser")
        describe_markup(soup, resp.text)
        paths = sorted({a["href"] for a in soup.find_all("a", href=True)
                        if a["href"].startswith("/content/")})
        print(f"    /content/ links: {len(paths)}")
        for path in paths[:8]:
            print(f"      {path}")

        # Follow a couple and show what the parser is working with.
        for path in paths[:3]:
            page_url = tecumseh.HERALD_BASE + path
            try:
                page = tecumseh._session.get(page_url, timeout=20)
            except Exception as e:
                print(f"    {path}: ERROR {e}")
                continue
            page_soup = BeautifulSoup(page.text, "html.parser")
            h1 = page_soup.find("h1")
            text = " ".join(page_soup.get_text(" ", strip=True).split())
            print(f"\n    --- {page_url} -> {page.status_code}")
            print(f"      h1: {h1.get_text(strip=True) if h1 else None!r}")
            print(f"      text[:400]: {text[:400]}")
            dt_match = tecumseh._HERALD_DT_RE.search(text)
            date_match = tecumseh._HERALD_DATE_ONLY_RE.search(text)
            print(f"      date+time regex: {dt_match.groups() if dt_match else None}")
            print(f"      date-only regex: {date_match.group(1) if date_match else None}")
            print(f"      parser returns: {tecumseh._parse_herald_event_page(page_url)}")


def probe_annarbor_widen() -> None:
    """What does the widened in-page query actually come back with?"""
    from collectors.local import annarbor

    head("annarbor — the widened in-page query, verbatim")
    today = date.today()
    window = [f"{today.isoformat()}T00:00:00.000Z",
              f"{(today + timedelta(days=7)).isoformat()}T23:59:59.000Z"]

    soup, payloads, widened = annarbor.eventpage.render_page_capturing(
        annarbor.BASE_URL,
        capture_pattern=annarbor._API_PATTERN,
        wait_selectors=annarbor.WAIT_SELECTORS,
        evaluate=annarbor._WIDER_QUERY_JS.strip(),
        evaluate_args=window,
    )
    print(f"  window: {window}")
    print(f"  the page's own API calls captured: {len(payloads)}")
    for payload in payloads:
        print(f"    first-screen docs: {len(annarbor._docs_of(payload))}")
    print(f"  widened result type: {type(widened).__name__}")
    if isinstance(widened, dict):
        print(f"    keys: {sorted(widened)}")
        print(f"    error: {widened.get('error')!r}")
        print(f"    body: {str(widened.get('body'))[:300]!r}")
        docs = annarbor._docs_of(widened.get("data"))
        print(f"    widened docs: {len(docs)}")
        for doc in docs[:8]:
            print(f"      {doc.get('date')} | {str(doc.get('title'))[:50]}")
    else:
        print(f"    raw: {str(widened)[:300]}")


def main() -> None:
    wanted = [a.lower() for a in sys.argv[1:]] or list(SITES) + ["tca"]

    for name, url in SITES.items():
        if name not in wanted:
            continue
        try:
            probe_static(name, url)
        except Exception as e:
            print(f"  static probe blew up: {type(e).__name__}: {e}")
        try:
            probe_rendered(name, url)
        except Exception as e:
            print(f"  rendered probe blew up: {type(e).__name__}: {e}")

    if "tca" in wanted:
        try:
            probe_tca()
        except Exception as e:
            print(f"  tca probe blew up: {type(e).__name__}: {e}")

    round2 = {
        "simpleview": probe_simpleview_token,
        "xhr-annarbor": lambda: _capture_xhr(
            "https://www.annarbor.org/events/", r"rest_v2|events_by_date", "annarbor"),
        "xhr-adrian": lambda: _capture_xhr(
            "https://events.yodel.today/y/widget/699331672d0ab3b826bf79e5",
            r"api|json|event", "adrian/yodel"),
        "vbo": probe_vbo_fragment,
        "tecumseh-markup": probe_tecumseh_markup,
        "annarbor-query": probe_annarbor_query,
        "yodel": probe_yodel_api,
        "yodel-static": probe_yodel_static,
        "annarbor-api": probe_annarbor_api,
        "vbo-key": probe_vbo_loadplugin,
        "collect": probe_collectors,
        "herald": probe_herald,
        "annarbor-widen": probe_annarbor_widen,
    }
    for name, fn in round2.items():
        if name in wanted:
            try:
                fn()
            except Exception as e:
                print(f"  {name} probe blew up: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
