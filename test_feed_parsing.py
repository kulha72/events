#!/usr/bin/env python3
"""
Tests for the layered event extraction that replaced the single-selector scrapers.

The bug these cover: every local scraper bound to exactly one CMS's class
names — `article.type-tribe_events` for the visitor bureaus, `div.event` for
downtown Tecumseh, `.EventListWrapper` for the theatre. All four fetched real
pages and matched nothing on the same morning, because a re-theme or a switch
to client-side rendering is indistinguishable from a quiet week when the
selector is the only thing you know how to read.

These pin down that a page's events are found through whichever layer survives,
that the report says which layer that was, and that the two "zero events but
nothing is broken" cases stay out of the failure list.

Run: python test_feed_parsing.py
"""

import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

import errors
from collectors.local import eventpage
from collectors.esports.pandascore import PandaScoreCollector, _fetch_all_matches
from models.event import Event, EventCategory

TZ = ZoneInfo("America/Detroit")

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {label}")
    else:
        print(f"  FAIL {label}{': ' + detail if detail else ''}")
        _failures.append(label)


# ── Fixtures ─────────────────────────────────────────────────────────────────

JSONLD_PAGE = """
<html><head><title>Events | Visit Somewhere</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"WebPage","name":"Events"},
  {"@type":"Event","name":"Riverfest &amp; Fireworks",
   "startDate":"2026-08-20T18:00:00-04:00","endDate":"2026-08-20T22:00:00-04:00",
   "url":"https://example.org/e/riverfest",
   "location":{"@type":"Place","name":"Riverside Park",
               "address":{"@type":"PostalAddress","streetAddress":"1 River Rd",
                          "addressLocality":"Adrian","addressRegion":"MI"}}},
  {"@type":"MusicEvent","name":"Concert in the Park","startDate":"2026-08-22",
   "url":"https://example.org/e/concert","location":"Bandshell"}
]}
</script></head><body><p>hello</p></body></html>
"""

MICRODATA_PAGE = """
<html><body>
<div itemscope itemtype="https://schema.org/TheaterEvent">
  <a itemprop="url" href="https://example.org/e/play">
    <span itemprop="name">A Midsummer Night's Dream</span></a>
  <meta itemprop="startDate" content="2026-08-21T19:30:00-04:00">
  <span itemprop="location">Croswell Opera House</span>
</div>
</body></html>
"""

TRIBE_PAGE = """
<html><body><div class="tribe-events-loop">
  <article class="type-tribe_events">
    <h3><a class="tribe-event-url" href="/e/market">Farmers Market</a></h3>
    <time datetime="2026-08-19T09:00:00">Wed, August 19 @ 9:00 am</time>
    <div class="tribe-venue">Downtown Lot</div>
  </article>
</div></body></html>
"""

# The shape the visitor bureaus reportedly return now: chrome, no events.
SPA_SHELL = """
<html><head><title>Events</title></head>
<body><nav>Home About Stay Eat</nav><div id="root"></div>
<script src="/app.js"></script><script src="/vendor.js"></script>
<footer>Contact us</footer></body></html>
"""

DOWNTOWN_RETHEMED = """
<html><body>
<div class="events-list">
  <div class="event-item">
    <h3 class="event-item__title"><a href="/events/sidewalk-sale">Sidewalk Sale</a></h3>
    <span class="event-item__date">August 20, 2026 10:00am</span>
    <span class="event-item__venue">Chicago Blvd</span>
  </div>
</div>
</body></html>
"""


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ── Extraction layers ────────────────────────────────────────────────────────

def test_jsonld_layer():
    """JSON-LD survives a re-theme, so it is tried before any CSS selector."""
    print("\n[test_jsonld_layer]")
    events = eventpage.parse_jsonld(soup_of(JSONLD_PAGE), TZ, "Adrian, MI")
    check("both events found", len(events) == 2, str(len(events)))

    riverfest = events[0]
    check("HTML entity in the title is unescaped",
          riverfest["title"] == "Riverfest & Fireworks", riverfest["title"])
    check("start time parsed with its offset",
          riverfest["start_dt"] == datetime(2026, 8, 20, 18, tzinfo=timezone(timedelta(hours=-4))),
          str(riverfest["start_dt"]))
    check("end time kept", riverfest["end_dt"] is not None, str(riverfest["end_dt"]))
    check("Place name and address flattened",
          riverfest["location"] == "Riverside Park, 1 River Rd, Adrian, MI",
          riverfest["location"])

    concert = events[1]
    check("MusicEvent counts as an event", concert["title"] == "Concert in the Park")
    check("date-only start becomes local midnight",
          concert["start_dt"] == datetime(2026, 8, 22, 0, 0, tzinfo=TZ), str(concert["start_dt"]))


def test_microdata_layer():
    """The older inline form of the same data, for sites that never moved to JSON-LD."""
    print("\n[test_microdata_layer]")
    events = eventpage.parse_microdata(soup_of(MICRODATA_PAGE), TZ)
    check("event found", len(events) == 1, str(len(events)))
    if events:
        check("name read from itemprop", events[0]["title"] == "A Midsummer Night's Dream")
        check("startDate read from the content attr, not the text",
              events[0]["start_dt"].hour == 19, str(events[0]["start_dt"]))
        check("url read from href", events[0]["url"].endswith("/e/play"), events[0]["url"])


def test_selector_layer_still_reads_tribe_markup():
    """The old markup must keep working — this is a widening, not a replacement."""
    print("\n[test_selector_layer_still_reads_tribe_markup]")
    events = eventpage.parse_selectors(
        soup_of(TRIBE_PAGE), TZ, "Adrian, MI", base_url="https://www.visitlenawee.com/events/"
    )
    check("event found", len(events) == 1, str(len(events)))
    if events:
        check("title read", events[0]["title"] == "Farmers Market", events[0]["title"])
        check("time read from the datetime attr",
              events[0]["start_dt"] == datetime(2026, 8, 19, 9, 0, tzinfo=TZ), str(events[0]["start_dt"]))
        check("relative href made absolute",
              events[0]["url"] == "https://www.visitlenawee.com/e/market", events[0]["url"])
        check("venue read", events[0]["location"] == "Downtown Lot", events[0]["location"])


def test_selector_layer_reads_unfamiliar_markup():
    """A theme that never heard of The Events Calendar still parses."""
    print("\n[test_selector_layer_reads_unfamiliar_markup]")
    events = eventpage.parse_selectors(
        soup_of(DOWNTOWN_RETHEMED), TZ, base_url="https://www.downtowntecumseh.com/events/"
    )
    check("generic event-item markup matched", len(events) == 1, str(len(events)))
    if events:
        check("title read", events[0]["title"] == "Sidewalk Sale", events[0]["title"])
        check("human-readable date parsed",
              events[0]["start_dt"].date() == date(2026, 8, 20), str(events[0]["start_dt"]))


def test_layers_are_tried_in_order_of_durability():
    """A page carrying both forms is read from the one that survives a re-theme."""
    print("\n[test_layers_are_tried_in_order_of_durability]")
    both = JSONLD_PAGE.replace("<body><p>hello</p></body>", f"<body>{TRIBE_PAGE}</body>")
    events, how = eventpage.extract_all(
        soup_of(both), TZ, "", eventpage.DEFAULT_ITEM_SELECTORS, "https://example.org/events/"
    )
    check("JSON-LD wins over selectors", how == "JSON-LD", how)
    check("its events are the ones returned", len(events) == 2, str(len(events)))

    events, how = eventpage.extract_all(
        soup_of(TRIBE_PAGE), TZ, "", eventpage.DEFAULT_ITEM_SELECTORS, "https://example.org/events/"
    )
    check("falls through to selectors when there is no structured data",
          how == "CSS selectors", how)


# ── Tribe REST ───────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    """Records requests and replays canned responses, keyed by URL substring."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, dict(params or {})))
        for fragment, response in self.routes.items():
            if fragment in url:
                if callable(response):
                    return response(params or {})
                return response
        return FakeResponse(None, status=404)


def test_tribe_rest_is_preferred_and_distinguishes_absent_from_empty():
    print("\n[test_tribe_rest_is_preferred_and_distinguishes_absent_from_empty]")
    today, end = date(2026, 8, 15), date(2026, 8, 22)

    session = FakeSession({"/wp-json/tribe/events/v1/events": FakeResponse({"events": [
        {"title": "Art Fair &#8211; Day 1", "start_date": "2026-08-20 10:00:00",
         "end_date": "2026-08-20 18:00:00", "url": "https://example.org/e/art",
         "venue": {"venue": "Main Street"}},
    ]})})
    events = eventpage.fetch_tribe_rest(session, "https://www.visitannarbor.org/events/", today, end, TZ)
    check("events returned", events and len(events) == 1, str(events))
    if events:
        check("start parsed into local tz",
              events[0]["start_dt"] == datetime(2026, 8, 20, 10, tzinfo=TZ), str(events[0]["start_dt"]))
        check("venue read", events[0]["location"] == "Main Street", events[0]["location"])
    check("queried the right host",
          session.calls[0][0] == "https://www.visitannarbor.org/wp-json/tribe/events/v1/events",
          session.calls[0][0])
    check("range passed through",
          session.calls[0][1]["start_date"] == "2026-08-15", str(session.calls[0][1]))

    # A working endpoint reporting an empty week must not read as a missing one.
    empty = FakeSession({"/wp-json/": FakeResponse({"code": "tribe-events-rest-no-events"})})
    check("empty week is [] not None",
          eventpage.fetch_tribe_rest(empty, "https://x.org/events/", today, end, TZ) == [])

    missing = FakeSession({})
    check("absent endpoint is None",
          eventpage.fetch_tribe_rest(missing, "https://x.org/events/", today, end, TZ) is None)


# ── Orchestration and reporting ──────────────────────────────────────────────

def test_scrape_falls_back_and_reports_the_degraded_path():
    """Losing the REST API is survivable, but it should not be silent."""
    print("\n[test_scrape_falls_back_and_reports_the_degraded_path]")
    errors.clear()

    session = FakeSession({"/events/": FakeResponse(None)})
    session.routes["/events/"] = FakeResponse(None)

    # Static page carries only the old Tribe markup: parseable, but fragile.
    class HtmlSession(FakeSession):
        def get(self, url, params=None, timeout=None, headers=None):
            self.calls.append((url, dict(params or {})))
            if "/wp-json/" in url:
                return FakeResponse(None)
            resp = FakeResponse({})
            resp.text = TRIBE_PAGE
            return resp

    raw = eventpage.scrape_calendar(
        source="adrian", session=HtmlSession({}), page_url="https://www.visitlenawee.com/events/",
        today=date(2026, 8, 15), lookahead_days=7, tz=TZ, default_location="Adrian, MI",
        site_label="visitlenawee.com", allow_render=False,
    )
    check("events parsed from the HTML layer", len(raw) == 1, str(len(raw)))

    health = errors.summary()
    check("not reported as a failure", not health["failures"], str(health["failures"]))
    check("not reported as suspect", not health["suspect"], str(health["suspect"]))
    check("reported as degraded", len(health["degraded"]) == 1, str(health["degraded"]))
    if health["degraded"]:
        check("degraded note names the layer",
              "CSS selectors" in health["degraded"][0]["reason"], health["degraded"][0]["reason"])


def test_empty_shell_is_suspect_and_says_what_it_saw():
    """The reported failure mode: a real fetch, an empty parse, no exception."""
    print("\n[test_empty_shell_is_suspect_and_says_what_it_saw]")
    errors.clear()

    class ShellSession(FakeSession):
        def get(self, url, params=None, timeout=None, headers=None):
            if "/wp-json/" in url:
                return FakeResponse(None)
            resp = FakeResponse({})
            resp.text = SPA_SHELL
            return resp

    raw = eventpage.scrape_calendar(
        source="annarbor", session=ShellSession({}), page_url="https://www.visitannarbor.org/events/",
        today=date(2026, 8, 15), lookahead_days=7, tz=TZ, site_label="visitannarbor.org",
        allow_render=False,
    )
    check("no events", raw == [])

    health = errors.summary()
    check("classified as suspect", len(health["suspect"]) == 1, str(health["suspect"]))
    if health["suspect"]:
        reason = health["suspect"][0]["reason"]
        check("names the site", "visitannarbor.org" in reason, reason)
        check("says every strategy was tried", "REST" in reason and "static HTML" in reason, reason)
        check("identifies the empty SPA root", "SPA root" in reason, reason)
        check("no longer blames tribe-events specifically", "tribe-events item" not in reason, reason)


def test_describe_page_distinguishes_the_empty_page_shapes():
    print("\n[test_describe_page_distinguishes_the_empty_page_shapes]")
    check("SPA shell called out as needing rendering",
          "needs rendering" in eventpage.describe_page(soup_of(SPA_SHELL)),
          eventpage.describe_page(soup_of(SPA_SHELL)))

    wall = "<html><head><title>Just a moment...</title></head><body>Checking</body></html>"
    check("bot challenge recognised",
          "bot-challenge" in eventpage.describe_page(soup_of(wall)),
          eventpage.describe_page(soup_of(wall)))


# ── Window filtering ─────────────────────────────────────────────────────────

def _event(day: date, title: str = "Show") -> Event:
    return Event(
        id=f"t:{title}:{day}", title=title, category=EventCategory.LOCAL,
        start=datetime(day.year, day.month, day.day, 19, 30, tzinfo=TZ).astimezone(timezone.utc),
        source="tca",
    )


def test_everything_beyond_the_window_reads_as_idle_not_broken():
    """A theatre that books months ahead is not a broken feed."""
    print("\n[test_everything_beyond_the_window_reads_as_idle_not_broken]")
    errors.clear()
    today = date(2026, 8, 15)

    kept = eventpage.within_window(
        [_event(date(2026, 10, 3), "Fall Play"), _event(date(2026, 11, 14), "Panto")],
        today, 7, TZ, "tca",
    )
    check("nothing kept", kept == [], str(kept))

    health = errors.summary()
    check("not a failure", not health["failures"], str(health["failures"]))
    check("not suspect", not health["suspect"], str(health["suspect"]))
    check("reported as idle", len(health["idle"]) == 1, str(health["idle"]))
    check("stays out of the unexplained-empty list",
          "tca" not in health["empty_sources"], str(health["empty_sources"]))
    if health["idle"]:
        check("says when the next show is", "Oct 3" in health["idle"][0]["reason"],
              health["idle"][0]["reason"])


def test_window_keeps_todays_and_in_range_events():
    print("\n[test_window_keeps_todays_and_in_range_events]")
    errors.clear()
    today = date(2026, 8, 15)
    kept = eventpage.within_window(
        [_event(date(2026, 8, 15), "Tonight"), _event(date(2026, 8, 21), "Next Friday"),
         _event(date(2026, 7, 1), "Last month"), _event(date(2026, 12, 1), "December")],
        today, 7, TZ, "tca",
    )
    check("two events kept", len(kept) == 2, str([e.title for e in kept]))
    check("no idle note when something was kept", not errors.summary()["idle"])


# ── PandaScore ───────────────────────────────────────────────────────────────

def _match(mid: int, league: str, when: str) -> dict:
    return {
        "id": mid, "scheduled_at": when, "league": {"name": league},
        "opponents": [{"opponent": {"name": f"T{mid}a"}}, {"opponent": {"name": f"T{mid}b"}}],
    }


def test_league_filter_looks_past_the_first_page():
    """The tier-B circuits fill page 1 on their own; the real fixture is on page 2."""
    print("\n[test_league_filter_looks_past_the_first_page]")
    errors.clear()

    page1 = [_match(i, "CCT Europe", "2026-08-16T12:00:00Z") for i in range(50)]
    page2 = [_match(100, "BLAST Premier", "2026-08-18T17:00:00Z")]

    import collectors.esports.pandascore as ps

    class PagedSession:
        def __init__(self):
            self.pages_requested = []

        def get(self, url, headers=None, params=None, timeout=None):
            page = params.get("page", 1)
            self.pages_requested.append(page)
            return FakeResponse(page1 if page == 1 else (page2 if page == 2 else []))

    original = ps._session
    ps._session = PagedSession()
    try:
        matches = _fetch_all_matches("csgo", {}, date(2026, 8, 15), date(2026, 8, 22))
        check("kept paging past the full first page", len(matches) == 51, str(len(matches)))
        check("stopped once a short page came back",
              ps._session.pages_requested == [1, 2], str(ps._session.pages_requested))
    finally:
        ps._session = original

    collector = PandaScoreCollector({"esports": {"games": [
        {"name": "CS2", "source": "pandascore", "leagues": ["BLAST", "ESL Pro League"]},
    ]}})
    collector.api_key = "test-key"

    ps._session = PagedSession()
    try:
        events = collector.collect(date(2026, 8, 15), 7)
    finally:
        ps._session = original

    check("the BLAST match on page 2 is collected", len(events) == 1, str(len(events)))
    check("no false alarm raised", not errors.summary()["suspect"], str(errors.summary()["suspect"]))


def test_quiet_league_is_idle_but_a_misspelled_one_is_suspect():
    """'Filter matched nothing' is a config bug only when the league isn't real."""
    print("\n[test_quiet_league_is_idle_but_a_misspelled_one_is_suspect]")
    import collectors.esports.pandascore as ps

    matches = [_match(i, "CCT Europe", "2026-08-16T12:00:00Z") for i in range(3)]

    class LeagueSession:
        def __init__(self, known_names):
            self.known = known_names

        def get(self, url, headers=None, params=None, timeout=None):
            if "/leagues" in url:
                name = params.get("search[name]", "")
                return FakeResponse([{"id": 1, "name": name}] if name in self.known else [])
            return FakeResponse(matches if params.get("page", 1) == 1 else [])

    original = ps._session

    # Both leagues exist — a week off is not a breakage.
    errors.clear()
    collector = PandaScoreCollector({"esports": {"games": [
        {"name": "CS2", "source": "pandascore", "leagues": ["BLAST", "ESL Pro League"]},
    ]}})
    collector.api_key = "test-key"
    ps._session = LeagueSession({"BLAST", "ESL Pro League"})
    try:
        collector.collect(date(2026, 8, 15), 7)
    finally:
        ps._session = original

    health = errors.summary()
    check("real-but-quiet leagues report idle", len(health["idle"]) == 1, str(health["idle"]))
    check("and not suspect", not health["suspect"], str(health["suspect"]))
    if health["idle"]:
        check("idle note names the configured leagues",
              "BLAST" in health["idle"][0]["reason"], health["idle"][0]["reason"])

    # A league PandaScore has never heard of can never match — that is actionable.
    errors.clear()
    collector = PandaScoreCollector({"esports": {"games": [
        {"name": "CS2", "source": "pandascore", "leagues": ["BLST Premeir"]},
    ]}})
    collector.api_key = "test-key"
    ps._session = LeagueSession({"BLAST"})
    try:
        collector.collect(date(2026, 8, 15), 7)
    finally:
        ps._session = original

    health = errors.summary()
    check("unknown league reports suspect", len(health["suspect"]) == 1, str(health["suspect"]))
    if health["suspect"]:
        check("names the offending entry",
              "BLST Premeir" in health["suspect"][0]["reason"], health["suspect"][0]["reason"])


def main() -> int:
    print("Feed parsing tests")
    test_jsonld_layer()
    test_microdata_layer()
    test_selector_layer_still_reads_tribe_markup()
    test_selector_layer_reads_unfamiliar_markup()
    test_layers_are_tried_in_order_of_durability()
    test_tribe_rest_is_preferred_and_distinguishes_absent_from_empty()
    test_scrape_falls_back_and_reports_the_degraded_path()
    test_empty_shell_is_suspect_and_says_what_it_saw()
    test_describe_page_distinguishes_the_empty_page_shapes()
    test_everything_beyond_the_window_reads_as_idle_not_broken()
    test_window_keeps_todays_and_in_range_events()
    test_league_filter_looks_past_the_first_page()
    test_quiet_league_is_idle_but_a_misspelled_one_is_suspect()
    errors.clear()

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
