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
import re
import sys
from collections import Counter
from datetime import date, timedelta

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
    }
    for name, fn in round2.items():
        if name in wanted:
            try:
                fn()
            except Exception as e:
                print(f"  {name} probe blew up: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
