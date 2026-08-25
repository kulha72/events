#!/usr/bin/env python3
"""
Tests for the four local sources after each of them broke in a different way.

What actually happened, and what these pin down:

  tecumseh  Cloudflare answered Playwright's default headless User-Agent with
            an interstitial, so the scraper parsed 392 characters of "Just a
            moment..." — a challenge page has to be recognised and waited out.
  tca       The wrapper page's `load` event does not fire within 15s once
            reCAPTCHA is on it, so the collector gave up before the iframe it
            was waiting for existed. The widget's list endpoint answers a
            plain request, and its session key is resolvable.
  annarbor  The calendar is not in the page at all; it arrives as JSON from a
            Simpleview API, timestamped as the end of the local day.
  adrian    The calendar moved into a third-party Yodel iframe, so the events
            were never in the document the scraper was reading.

Run: python test_local_sources.py
"""

import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

import errors

from collectors.local import adrian, annarbor, eventpage, tca, tecumseh

TZ = ZoneInfo("America/Detroit")

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {label}")
    else:
        print(f"  FAIL {label}{': ' + detail if detail else ''}")
        _failures.append(label)


# ── Bot challenges ───────────────────────────────────────────────────────────

def test_challenge_pages_are_recognised():
    print("\n[test_challenge_pages_are_recognised]")
    check("Cloudflare's interstitial", eventpage._is_challenge_title("Just a moment..."))
    check("the other wording", eventpage._is_challenge_title("Attention Required! | Cloudflare"))
    check("case does not matter", eventpage._is_challenge_title("CHECKING YOUR BROWSER"))
    check(
        "a real page is not mistaken for one",
        not eventpage._is_challenge_title("Events | Downtown Tecumseh in Michigan"),
    )
    check("an empty title is not a challenge", not eventpage._is_challenge_title(""))


def test_renders_present_a_real_browser():
    print("\n[test_renders_present_a_real_browser]")
    # The bug was the default "HeadlessChrome" UA, which Cloudflare 403s.
    check("no HeadlessChrome in the User-Agent", "Headless" not in eventpage.BROWSER_UA)
    check("claims a desktop Chrome", "Chrome/" in eventpage.BROWSER_UA)


# ── Ann Arbor: the Simpleview events API ─────────────────────────────────────

API_PAYLOAD = {
    "docs": {
        "count": 252,
        "docs": [
            {
                "_id": "6a8d6458349da43599a98a12",
                "location": "Bløm Meadworks",
                "date": "2026-08-26T03:59:59.000Z",
                "startDate": "2023-12-12T05:00:00.000Z",
                "recurrence": "Recurring weekly on Tuesday",
                "recid": "13751",
                "title": "Babies & Brews",
                "url": "/event/babies-%26-brews/13751/",
                "city": "Ann Arbor",
                "listing": {"title": "Bløm Meadworks", "city": "Ann Arbor"},
            },
            {
                "_id": "6a849a77349da435996bf005",
                "location": "Chelsea Community Fairgrounds",
                "date": "2026-08-27T03:59:59.000Z",
                "startDate": "2026-08-25T04:00:00.000Z",
                "endDate": "2026-08-30T03:59:59.000Z",
                "recid": "25168",
                "title": "Chelsea Community Fair",
                "url": "/event/chelsea-community-fair/25168/",
                "city": "Chelsea",
            },
        ],
    }
}


def test_api_envelope_unwraps():
    print("\n[test_api_envelope_unwraps]")
    check("the nested docs envelope", len(annarbor._docs_of(API_PAYLOAD)) == 2)
    check("a flat docs list", len(annarbor._docs_of({"docs": [{"title": "x"}]})) == 1)
    check("nothing at all", annarbor._docs_of(None) == [])
    check("an error body", annarbor._docs_of({"error": "Invalid credentials"}) == [])


def test_end_of_local_day_timestamps_land_on_the_right_day():
    print("\n[test_end_of_local_day_timestamps_land_on_the_right_day]")
    # 2026-08-26T03:59:59Z is 11:59:59pm on the 25th in Detroit. Reading it as
    # a UTC date would file the event a day late, every time.
    check(
        "the 26th at 03:59:59 UTC is the 25th here",
        annarbor._local_day("2026-08-26T03:59:59.000Z") == date(2026, 8, 25),
    )
    check(
        "and the next one is the 26th",
        annarbor._local_day("2026-08-27T03:59:59.000Z") == date(2026, 8, 26),
    )
    check("junk is not a date", annarbor._local_day("not a date") is None)
    check("nor is nothing", annarbor._local_day(None) is None)


def test_api_docs_become_events():
    print("\n[test_api_docs_become_events]")
    events = annarbor._events_from_docs(annarbor._docs_of(API_PAYLOAD))
    check("both docs became events", len(events) == 2, str(events))
    if len(events) != 2:
        return

    first = events[0]
    check("title kept", first["title"] == "Babies & Brews")
    check(
        "filed on the local day, as all-day",
        first["start_dt"].date() == date(2026, 8, 25) and first["start_dt"].hour == 0,
        str(first["start_dt"]),
    )
    check(
        "relative url made absolute",
        first["url"] == "https://www.annarbor.org/event/babies-%26-brews/13751/",
        first["url"],
    )
    check(
        "town appended to a venue that does not name it",
        first["location"] == "Bløm Meadworks, Ann Arbor",
        first["location"],
    )
    check(
        "but not to a venue that already does",
        events[1]["location"] == "Chelsea Community Fairgrounds",
        events[1]["location"],
    )


def test_the_same_occurrence_is_not_listed_twice():
    print("\n[test_the_same_occurrence_is_not_listed_twice]")
    # The page's own first-screen call and the widened one overlap; merging
    # them must not double every event the reader sees.
    docs = annarbor._docs_of(API_PAYLOAD)
    events = annarbor._events_from_docs(docs + docs)
    check("duplicates collapsed", len(events) == 2, str(len(events)))


def test_a_doc_with_no_usable_date_is_dropped():
    print("\n[test_a_doc_with_no_usable_date_is_dropped]")
    events = annarbor._events_from_docs([
        {"title": "No date here", "url": "/event/x/1/"},
        {"date": "2026-08-26T03:59:59.000Z", "url": "/event/y/2/"},
    ])
    check("neither survives", events == [], str(events))


# ── Adrian: the Yodel embed ──────────────────────────────────────────────────

LENAWEE_PAGE = """
<html><head><title>Events Archive - Lenawee</title></head><body>
  <a href="https://events.yodel.today/y/widget/699331672d0ab3b826bf79e5/submit_event">
    Submit an event</a>
  <div id="yodel2-embed" data-src="https://events.yodel.today/y/widget/699331672d0ab3b826bf79e5"></div>
</body></html>
"""


def test_the_widget_id_is_read_off_the_bureau_page():
    print("\n[test_the_widget_id_is_read_off_the_bureau_page]")
    found = adrian._WIDGET_ID_RE.search(LENAWEE_PAGE)
    check("an embed is found", found is not None)
    check(
        "and it is the widget's id",
        found and found.group(1) == "699331672d0ab3b826bf79e5",
        found.group(1) if found else "",
    )
    check(
        "a page with no embed reports none",
        adrian._WIDGET_ID_RE.search("<html><body>no calendar here</body></html>") is None,
    )


YODEL_WIDGET = """
<html><head><title>Yodel - Lenawee, MI Event Calendar</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
 {"@type":"ListItem","position":1,"item":{
   "@type":"Event","name":"Michigan Watercolor Society Annual Exhibit",
   "startDate":"2026-08-26T10:00:00-04:00","endDate":"2026-08-30T17:00:00-04:00",
   "url":"https://events.yodel.today/y/event/Adrian-Center-for-the-Arts/x/6a5211a9",
   "location":{"@type":"Place","name":"Adrian Center for the Arts",
               "address":{"@type":"PostalAddress","addressLocality":"Adrian","addressRegion":"MI"}}}}
]}
</script></head><body><div id="eventContainer"></div></body></html>
"""


def test_the_widget_is_read_through_its_json_ld():
    print("\n[test_the_widget_is_read_through_its_json_ld]")
    events, how = eventpage.extract_all(
        BeautifulSoup(YODEL_WIDGET, "html.parser"),
        TZ, adrian.DEFAULT_LOCATION, eventpage.DEFAULT_ITEM_SELECTORS,
        "https://events.yodel.today/y/widget/699331672d0ab3b826bf79e5",
    )
    check("one event", len(events) == 1, str(events))
    check("through the durable layer", how == "JSON-LD", how)
    if not events:
        return
    check("title kept", events[0]["title"] == "Michigan Watercolor Society Annual Exhibit")
    check("start time kept", events[0]["start_dt"].hour == 10, str(events[0]["start_dt"]))
    check(
        "venue and town",
        events[0]["location"] == "Adrian Center for the Arts, Adrian, MI",
        events[0]["location"],
    )


# ── TCA: the VBO widget ──────────────────────────────────────────────────────

VBO_LIST = """
<html><body>
<div class="EventListWrapper EventListBgd clearfix EID203788 EDID718825" id="EDID718825">
  <div class="EventListDetails EventListColRight"><div class="EventListText">
    <h2 class="HeaderEventName">
      <a href="https://plugin.vbotickets.com/v5.0/event.asp?eid=203788">The Magic of Motown</a>
    </h2>
  </div></div>
  <div class="EventListExtra"><div class="EventListExtraText clearfix">
    <div class="TextEventDate FloatLeft">Fri, 9/18/2026 @ 7:30 PM</div>
    <div class="TextVenueName">Tecumseh Center for the Arts</div>
  </div></div>
</div>
<div class="EventListWrapper EventListBgd clearfix EID207363" id="EDID718900">
  <div class="EventListText">
    <h2 class="HeaderEventName"><a href="#">COMEDY NIGHT with John Heffron</a></h2>
  </div>
  <div class="TextEventDate FloatLeft">Sat, 10/17/2026 @ 7:30 PM</div>
</div>
<div class="EventListWrapper" id="EDID999999">
  <div class="EventListText"><h2 class="HeaderEventName"><a href="#">On sale soon</a></h2></div>
</div>
</body></html>
"""


def test_vbo_cards_parse():
    print("\n[test_vbo_cards_parse]")
    cards = BeautifulSoup(VBO_LIST, "html.parser").select(".EventListWrapper")
    check("three cards in the fixture", len(cards) == 3)
    events = tca._parse_cards(cards)
    check("the dateless card is dropped", len(events) == 2, str(len(events)))
    if len(events) != 2:
        return

    first = events[0]
    check("title kept", first["title"] == "The Magic of Motown", first["title"])
    check(
        "date and time read",
        (first["start_dt"].year, first["start_dt"].month, first["start_dt"].day,
         first["start_dt"].hour, first["start_dt"].minute) == (2026, 9, 18, 19, 30),
        str(first["start_dt"]),
    )
    check("local zone applied", first["start_dt"].tzinfo is not None)
    check("venue kept", first["location"] == "Tecumseh Center for the Arts")
    check(
        "links to the theatre, not the booking tab",
        first["url"] == tca.TICKETS_URL,
        first["url"],
    )
    check(
        "a card with no venue falls back to the theatre's address",
        events[1]["location"] == tca.DEFAULT_LOCATION,
        events[1]["location"],
    )


def test_structured_fallback_entries_pass_through_untouched():
    print("\n[test_structured_fallback_entries_pass_through_untouched]")
    # The JSON-LD fallback hands _parse_cards dicts rather than markup.
    already = {
        "title": "A Play",
        "start_dt": datetime(2026, 9, 18, 19, 30, tzinfo=TZ),
        "url": tca.TICKETS_URL,
        "location": "TCA",
    }
    check("passed straight through", tca._parse_cards([already]) == [already])
    check(
        "but one with no date is still dropped",
        tca._parse_cards([dict(already, start_dt=None)]) == [],
    )


LOADPLUGIN_BODY = """
<!DOCTYPE html><html><head><script type="text/javascript">
document.addEventListener("DOMContentLoaded", function () {
  window.parent.postMessage(JSON.stringify({
    type: "userSessionID", orgID: "3766",
    value: "0f0a024c-778a-42bb-b730-06793e983350" }), "*");
  window.location.href =
    "https://plugin.vbotickets.com/plugin/events?s=0f0a024c-778a-42bb-b730-06793e983350";
});
</script></head><body>&nbsp;</body></html>
"""

VBO_WRAPPER = """
<html><body><script>
var SiteID = "77C91F6E-127F-4F2A-825A-AC98BBE2743F"; var OrgID = "3766";
var Page = "ListEvents";
</script></body></html>
"""


def test_the_widget_key_is_resolvable():
    print("\n[test_the_widget_key_is_resolvable]")
    site = tca._SITE_ID_RE.search(VBO_WRAPPER)
    org = tca._ORG_ID_RE.search(VBO_WRAPPER)
    check("SiteID found", site and site.group(1) == "77C91F6E-127F-4F2A-825A-AC98BBE2743F")
    check("OrgID found", org and org.group(1) == "3766")

    key = tca._GUID_RE.search(LOADPLUGIN_BODY)
    check(
        "the session key comes back out of loadplugin",
        key and key.group(0) == "0f0a024c-778a-42bb-b730-06793e983350",
        key.group(0) if key else "",
    )
    check(
        "and it is what the list endpoint is asked for",
        "s=0f0a024c-778a-42bb-b730-06793e983350"
        in tca._list_url("0f0a024c-778a-42bb-b730-06793e983350"),
    )


# ── Tecumseh: the Herald half ────────────────────────────────────────────────

HERALD_MONTH = """
<html><head><title>| The Tecumseh Herald</title></head><body>
<div class="view-content">
  <div class="views-row views-row-odd views-row-first">
    <a href="/content/clinton-boys-soccer-aiming-big-season">Clinton boys soccer</a>
  </div>
</div>
<footer>
  <a href="/content/subscriptions">Subscriptions</a>
  <a href="/content/classifieds">Classifieds</a>
  <a href="/content/refund-policy">Refund policy</a>
</footer>
</body></html>
"""


class _FakeHeraldSession:
    """Serves the same month page for every month, as the Herald really does."""

    def __init__(self, pages):
        self.pages = pages
        self.headers = {}
        self.asked: list[str] = []

    def get(self, url, timeout=None):
        self.asked.append(url)
        body = self.pages(url) if callable(self.pages) else self.pages

        class Response:
            text = body

            def raise_for_status(self):
                return None

        return Response()


def _with_herald_session(pages):
    original = tecumseh._session
    tecumseh._session = _FakeHeraldSession(pages)
    try:
        errors.clear()
        return tecumseh._scrape_herald(months_ahead=2), errors.summary()
    finally:
        tecumseh._session = original
        errors.clear()


def test_an_empty_herald_calendar_reads_as_idle_not_broken():
    print("\n[test_an_empty_herald_calendar_reads_as_idle_not_broken]")
    # Every month returning the identical link set means those links are the
    # site's nav and the calendar is simply empty.
    events, health = _with_herald_session(HERALD_MONTH)
    check("no events", events == [])
    check("not reported as suspect", health["suspect"] == [], str(health["suspect"]))
    check("reported as idle", [s["source"] for s in health["idle"]] == ["tecumseh"],
          str(health["idle"]))
    check(
        "and says why",
        health["idle"] and "no entries" in health["idle"][0]["reason"],
        str(health["idle"]),
    )
    check("no failures", health["failures"] == [])


def test_a_herald_calendar_that_differs_by_month_is_still_suspect():
    print("\n[test_a_herald_calendar_that_differs_by_month_is_still_suspect]")
    # Different links each month means there is a calendar and we failed to
    # read it — the case that should still raise an eyebrow.
    def pages(url):
        month = url.rsplit("/", 1)[-1]
        return HERALD_MONTH.replace(
            "clinton-boys-soccer-aiming-big-season", f"something-in-{month}"
        )

    events, health = _with_herald_session(pages)
    check("no events", events == [])
    check("reported as suspect", [s["source"] for s in health["suspect"]] == ["tecumseh"],
          str(health["suspect"]))
    check("not reported as idle", health["idle"] == [], str(health["idle"]))


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed: {', '.join(_failures)}")
        sys.exit(1)
    print("All checks passed.")
