#!/usr/bin/env python3
"""
Tests for the time of day on an event, which the digest kept losing to midnight.

Three separate faults put "12 AM" next to listings that advertise a real start:

  1. The Tecumseh time regexes were written `(?:am|pm)` hard against the
     digits, so they matched "7pm" and nothing else. "7:00 pm", "7 PM" and
     "7 p.m." — how the sites actually write it — read as no time at all, and
     the collector filed those events at local midnight.

  2. The layered page parser stopped at the first element that produced a
     date. The Events Calendar puts a date-only `<time datetime="2026-08-20">`
     first and spells "August 20 @ 7:00 pm" out in the schedule text beside
     it, so the 7pm was never read.

  3. The formatters worked out "is this all-day?" from `event.start.hour or
     event.start.minute` — against a UTC datetime. An all-day listing (local
     midnight, 04:00/05:00 UTC) came out as "12 AM ET", and an 8 PM ET
     fixture (00:00 UTC the next day) came out as "All day". Both wrong, in
     opposite directions.

The fix behind these checks is that a collector now states `all_day` rather
than leaving the formatters to infer it from a clock they were reading in the
wrong zone.

Run: python test_event_times.py
"""

import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from collectors.local import eventpage
from collectors.local import tecumseh
from formatters.email_formatter import _event_display
from formatters.telegram_formatter import _fmt_time as _telegram_time
from models.event import Event, EventCategory

TZ = ZoneInfo("America/Detroit")

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {label}")
    else:
        print(f"  FAIL {label}{': ' + detail if detail else ''}")
        _failures.append(label)


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ── Fixtures ─────────────────────────────────────────────────────────────────

# The reported shape: a machine-readable date with no time on it, and the
# actual start time only in the text a human reads.
DATE_ONLY_TIME_ATTR = """
<html><body><div class="tribe-events-loop">
  <article class="type-tribe_events">
    <h3><a class="tribe-event-url" href="/e/show">Evening Concert</a></h3>
    <time datetime="2026-08-20">August 20</time>
    <div class="tribe-event-schedule-details">Thu, August 20 @ 7:00 pm</div>
    <div class="tribe-venue">Bandshell</div>
  </article>
</div></body></html>
"""

# Date in one element, clock time in another, neither complete on its own.
SPLIT_DATE_AND_TIME = """
<html><body>
<div class="event-item">
  <h3 class="event-item__title"><a href="/e/sale">Sidewalk Sale</a></h3>
  <span class="event-item__date">August 20, 2026</span>
  <span class="event-item__time">10:30 am</span>
</div>
</body></html>
"""

# Nothing anywhere names a clock time: genuinely an all-day listing.
NO_TIME_ANYWHERE = """
<html><body>
<div class="event-item">
  <h3 class="event-item__title"><a href="/e/fair">County Fair</a></h3>
  <span class="event-item__date">August 21, 2026</span>
  <span class="event-item__venue">Fairgrounds</span>
</div>
</body></html>
"""

JSONLD_MIXED = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Event","name":"Riverfest","startDate":"2026-08-20T18:00:00-04:00",
  "url":"https://example.org/e/r"},
 {"@type":"MusicEvent","name":"Concert","startDate":"2026-08-22",
  "url":"https://example.org/e/c"}
]}</script></head><body></body></html>
"""


# ── The page-parsing layers ──────────────────────────────────────────────────

def test_a_date_only_time_attr_does_not_hide_the_real_start():
    """The first element that parses is not necessarily the one with the time."""
    print("\n[test_a_date_only_time_attr_does_not_hide_the_real_start]")
    events = eventpage.parse_selectors(
        soup_of(DATE_ONLY_TIME_ATTR), TZ, base_url="https://example.org/events/"
    )
    check("event found", len(events) == 1, str(len(events)))
    if not events:
        return
    check("kept 7:00 pm rather than the attribute's midnight",
          events[0]["start_dt"] == datetime(2026, 8, 20, 19, 0, tzinfo=TZ),
          str(events[0]["start_dt"]))
    check("not reported as all-day", events[0]["all_day"] is False, str(events[0]["all_day"]))


def test_date_and_time_can_come_from_different_elements():
    print("\n[test_date_and_time_can_come_from_different_elements]")
    events = eventpage.parse_selectors(
        soup_of(SPLIT_DATE_AND_TIME), TZ, base_url="https://example.org/"
    )
    check("event found", len(events) == 1, str(len(events)))
    if not events:
        return
    check("date from one element, clock from the other",
          events[0]["start_dt"] == datetime(2026, 8, 20, 10, 30, tzinfo=TZ),
          str(events[0]["start_dt"]))
    check("not all-day", events[0]["all_day"] is False, str(events[0]["all_day"]))


def test_a_listing_with_no_time_is_all_day_not_midnight():
    """The other half: midnight must stay available for listings that earn it."""
    print("\n[test_a_listing_with_no_time_is_all_day_not_midnight]")
    events = eventpage.parse_selectors(
        soup_of(NO_TIME_ANYWHERE), TZ, base_url="https://example.org/"
    )
    check("event found", len(events) == 1, str(len(events)))
    if not events:
        return
    check("sorts at local midnight",
          events[0]["start_dt"] == datetime(2026, 8, 21, 0, 0, tzinfo=TZ),
          str(events[0]["start_dt"]))
    check("flagged all-day", events[0]["all_day"] is True, str(events[0]["all_day"]))


def test_jsonld_separates_a_bare_date_from_a_timestamp():
    print("\n[test_jsonld_separates_a_bare_date_from_a_timestamp]")
    events = eventpage.parse_jsonld(soup_of(JSONLD_MIXED), TZ)
    check("both events found", len(events) == 2, str(len(events)))
    if len(events) != 2:
        return
    check("a full timestamp is a timed event", events[0]["all_day"] is False, str(events[0]))
    check("a bare date is all-day", events[1]["all_day"] is True, str(events[1]))
    check("the bare date still sorts at local midnight",
          events[1]["start_dt"] == datetime(2026, 8, 22, 0, 0, tzinfo=TZ),
          str(events[1]["start_dt"]))


def test_tribe_rest_honours_its_own_all_day_flag():
    """Tribe writes an all-day start as "00:00:00", so only its flag can tell."""
    print("\n[test_tribe_rest_honours_its_own_all_day_flag]")

    class Resp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class Session:
        def __init__(self, payload):
            self._p = payload

        def get(self, url, params=None, timeout=None):
            return Resp(self._p)

    session = Session({"events": [
        {"title": "Art Fair", "start_date": "2026-08-20 00:00:00",
         "all_day": True, "url": "https://example.org/e/art", "venue": {}},
        {"title": "Evening Show", "start_date": "2026-08-21 19:30:00",
         "all_day": False, "url": "https://example.org/e/show", "venue": {}},
    ]})
    events = eventpage.fetch_tribe_rest(
        session, "https://example.org/events/", date(2026, 8, 15), date(2026, 8, 22), TZ
    )
    check("both returned", events and len(events) == 2, str(events))
    if not events or len(events) != 2:
        return
    check("the flagged one is all-day", events[0]["all_day"] is True, str(events[0]))
    check("the 7:30pm show is not", events[1]["all_day"] is False, str(events[1]))
    check("and keeps its time",
          events[1]["start_dt"] == datetime(2026, 8, 21, 19, 30, tzinfo=TZ),
          str(events[1]["start_dt"]))


def test_parse_start_needs_a_date_not_just_a_time():
    """A stray "7:00 pm" with no date must not place an event on its own."""
    print("\n[test_parse_start_needs_a_date_not_just_a_time]")
    parsed, all_day = eventpage.parse_start("7:00 pm", TZ)
    check("a bare time is not a start", parsed is None, str(parsed))

    parsed, all_day = eventpage.parse_start("August 20, 2026 7:00 pm", TZ)
    check("a date with a time is timed", parsed is not None and all_day is False, str(parsed))

    parsed, all_day = eventpage.parse_start("August 20, 2026", TZ)
    check("a date on its own is all-day", parsed is not None and all_day is True, str(parsed))

    parsed, all_day = eventpage.parse_start("Downtown Lot", TZ, fuzzy=True)
    check("prose with no date in it parses to nothing", parsed is None, str(parsed))


# ── Tecumseh's free-text times ───────────────────────────────────────────────

def test_the_ordinary_ways_of_writing_a_time_are_all_read():
    """"7:00 pm" is how the sites write it, and it used to match nothing."""
    print("\n[test_the_ordinary_ways_of_writing_a_time_are_all_read]")
    for line, expected in [
        ("Thursday, August 20, 2026, 7:00 pm", "7:00 pm"),
        ("Thursday, August 20, 2026, 7:00pm", "7:00pm"),
        ("Thursday, August 20, 2026 7 PM", "7 PM"),
        ("Friday, August 21, 2026, 7 p.m.", "7 p.m."),
    ]:
        start, _ = tecumseh._line_times(line)
        check(f"reads {expected!r}", start == expected, f"got {start!r} from {line!r}")

    for line, want_start, want_end in [
        ("Saturday, August 22, 10:00 am - 4:00 pm", "10:00 am", "4:00 pm"),
        ("Saturday, August 22, 10am-4pm", "10am", "4pm"),
        ("Saturday, August 22, 10 am to 4 pm", "10 am", "4 pm"),
    ]:
        start, end = tecumseh._line_times(line)
        check(f"reads the range in {line!r}", (start, end) == (want_start, want_end),
              f"got {(start, end)}")


def test_an_open_time_without_a_meridiem_takes_one_from_the_close():
    print("\n[test_an_open_time_without_a_meridiem_takes_one_from_the_close]")
    start, end = tecumseh._line_times("Saturday, August 22, 10 - 4pm")
    check("10 - 4pm opens in the morning", start is not None and "am" in start.lower(),
          f"got {start!r}")
    start, _ = tecumseh._line_times("Saturday, 1 - 5pm")
    check("1 - 5pm opens in the afternoon", start is not None and "pm" in start.lower(),
          f"got {start!r}")


def test_a_date_is_never_mistaken_for_a_time():
    print("\n[test_a_date_is_never_mistaken_for_a_time]")
    start, _ = tecumseh._line_times("Saturday, August 22, 2026")
    check("a bare date yields no time", start is None, f"got {start!r}")
    start, _ = tecumseh._line_times("August 20-22, 2026")
    check("a range of days is not a range of times", start is None, f"got {start!r}")


def test_downtown_when_text_resolves_to_the_advertised_hour():
    print("\n[test_downtown_when_text_resolves_to_the_advertised_hour]")
    entries = tecumseh._parse_downtown_when("Thursday, August 20, 2026, 7:00 pm")
    check("one entry", len(entries) == 1, str(entries))
    if entries:
        check("date read", entries[0]["start_date"] == date(2026, 8, 20), str(entries[0]))
        resolved = tecumseh._parse_time_str(entries[0]["start_time_str"],
                                            entries[0]["start_date"])
        check("resolves to 19:00 local", resolved is not None and resolved.hour == 19,
              str(resolved))

    entries = tecumseh._parse_downtown_when("Saturday, August 22, 2026\n10:00 am - 4:00 pm")
    check("a time on the next line still attaches",
          entries and entries[0]["start_time_str"] == "10:00 am", str(entries))

    entries = tecumseh._parse_downtown_when("Saturday, August 22, 2026")
    check("a date with no time stays timeless",
          entries and entries[0]["start_time_str"] is None, str(entries))


def test_herald_reads_a_range_a_lone_start_and_a_bare_date():
    print("\n[test_herald_reads_a_range_a_lone_start_and_a_bare_date]")
    start, end, all_day = tecumseh._parse_herald_datetimes(
        "Thursday, August 20, 2026, from 7:00 pm to 9:00 pm"
    )
    check("range start is 19:00", start is not None and start.hour == 19, str(start))
    check("range end is 21:00", end is not None and end.hour == 21, str(end))
    check("a range is not all-day", all_day is False, str(all_day))

    start, end, all_day = tecumseh._parse_herald_datetimes("August 20, 2026 at 7:00 pm")
    check("a lone start time is read", start is not None and start.hour == 19, str(start))
    check("and is not all-day", all_day is False, str(all_day))

    start, end, all_day = tecumseh._parse_herald_datetimes("August 20, 2026 — community potluck")
    check("a bare date still yields a date", start is not None, str(start))
    check("and is all-day", all_day is True, str(all_day))


# ── Formatting ───────────────────────────────────────────────────────────────

def _event(local: datetime, all_day: bool = False) -> Event:
    """An event as the collectors store one: UTC, converted from local wall time."""
    return Event(
        id="t", title="Test", category=EventCategory.LOCAL,
        start=local.astimezone(timezone.utc), all_day=all_day, source="test",
    )


def test_the_email_labels_all_day_without_inventing_midnight():
    print("\n[test_the_email_labels_all_day_without_inventing_midnight]")
    all_day = _event(datetime(2026, 8, 20, 0, 0, tzinfo=TZ), all_day=True)
    check("all-day reads 'All day'", _event_display(all_day, TZ)["time"] == "All day",
          _event_display(all_day, TZ)["time"])

    evening = _event(datetime(2026, 8, 20, 19, 0, tzinfo=TZ))
    check("7pm reads '7 PM ET'", _event_display(evening, TZ)["time"] == "7 PM ET",
          _event_display(evening, TZ)["time"])

    # 8 PM in Detroit is 00:00 UTC the next day. The old hour-and-minute test
    # ran against the UTC value and called this one all-day.
    eight = _event(datetime(2026, 8, 20, 20, 0, tzinfo=TZ))
    check("8 PM ET is not swallowed as all-day",
          _event_display(eight, TZ)["time"] == "8 PM ET", _event_display(eight, TZ)["time"])

    odd = _event(datetime(2026, 8, 20, 13, 5, tzinfo=TZ))
    check("an off-hour keeps its minutes", _event_display(odd, TZ)["time"] == "1:05 PM ET",
          _event_display(odd, TZ)["time"])

    midnight = _event(datetime(2026, 8, 20, 0, 0, tzinfo=TZ), all_day=False)
    check("a start that really is midnight still says so",
          _event_display(midnight, TZ)["time"] == "12 AM ET",
          _event_display(midnight, TZ)["time"])


def test_telegram_says_all_day_rather_than_1200_am():
    print("\n[test_telegram_says_all_day_rather_than_1200_am]")
    all_day = _event(datetime(2026, 8, 20, 0, 0, tzinfo=TZ), all_day=True)
    check("all-day reads 'All day'", _telegram_time(all_day, TZ) == "All day",
          _telegram_time(all_day, TZ))

    evening = _event(datetime(2026, 8, 20, 19, 0, tzinfo=TZ))
    check("7pm reads '7:00 PM'", _telegram_time(evening, TZ) == "7:00 PM",
          _telegram_time(evening, TZ))

    eight = _event(datetime(2026, 8, 20, 20, 0, tzinfo=TZ))
    check("8 PM ET stays 8 PM", _telegram_time(eight, TZ) == "8:00 PM",
          _telegram_time(eight, TZ))


def main() -> int:
    print("Event time-of-day tests")
    test_a_date_only_time_attr_does_not_hide_the_real_start()
    test_date_and_time_can_come_from_different_elements()
    test_a_listing_with_no_time_is_all_day_not_midnight()
    test_jsonld_separates_a_bare_date_from_a_timestamp()
    test_tribe_rest_honours_its_own_all_day_flag()
    test_parse_start_needs_a_date_not_just_a_time()
    test_the_ordinary_ways_of_writing_a_time_are_all_read()
    test_an_open_time_without_a_meridiem_takes_one_from_the_close()
    test_a_date_is_never_mistaken_for_a_time()
    test_downtown_when_text_resolves_to_the_advertised_hour()
    test_herald_reads_a_range_a_lone_start_and_a_bare_date()
    test_the_email_labels_all_day_without_inventing_midnight()
    test_telegram_says_all_day_rather_than_1200_am()

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
