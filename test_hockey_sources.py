#!/usr/bin/env python3
"""
Tests for the two hockey teams ESPN does not carry: Adrian College (Sidearm
calendar JSON) and the Toledo Walleye (the club's .ics feed plus the score
grid on its home page).

Fixtures are trimmed copies of what each site returned on 2026-11-15.

Run: python test_hockey_sources.py
"""

import sys
from datetime import date
from zoneinfo import ZoneInfo

from collectors.sports import sidearm, walleye

TZ = ZoneInfo("America/Detroit")

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {label}")
    else:
        print(f"  FAIL {label}{': ' + detail if detail else ''}")
        _failures.append(label)


ADRIAN = {
    "name": "Adrian Hockey",
    "league": "NCAA D-III",
    "source": "sidearm",
    "sidearm_site": "https://adrianbulldogs.com",
    "sidearm_sport_id": 13,
    "url": "https://adrianbulldogs.com/sports/mens-ice-hockey/schedule",
}
WALLEYE = {"name": "Walleye", "league": "ECHL", "source": "toledo_walleye"}

PLAYED = {
    "id": 24039, "date": "2026-11-14T17:00:00", "time": "5:00 PM ET",
    "conference": True, "location": "Lake Forest, Ill.", "location_indicator": "A",
    "opponent": {"title": "Lake Forest College"},
    "result": {"status": "W", "team_score": "3", "opponent_score": "0",
               "boxscore": {"url": "/boxscore.aspx?id=24039"},
               "recap": {"url": "/news/2026/11/14/recap.aspx"}},
    "facility": None,
}
UPCOMING_HOME = {
    "id": 24044, "date": "2026-12-04T19:00:00", "time": "7:00 PM",
    "conference": True, "location": "Adrian, Mich.", "location_indicator": "H",
    "opponent": {"title": "St. Norbert College"}, "result": None,
    "facility": {"title": "Arrington Ice Arena"},
}
NO_TIME = {
    "id": 24099, "date": "2027-03-28T00:00:00", "time": "TBA",
    "conference": False, "location": "TBD", "location_indicator": "N",
    "opponent": {"title": "NCAA Tournament National Championship"}, "result": {},
}

ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:https://www.toledowalleye.com/games/2026/11/15/indy-fuel\r\n"
    "SUMMARY:Toledo Walleye @ Indy Fuel\r\n"
    "DTSTART;TZID=America/New_York:20261115T160000\r\n"
    "DESCRIPTION:Toledo Walleye at Indy Fuel\r\n"
    "LOCATION:Fishers Event Center\\, Fishers\\, Indiana\r\n"
    "URL;VALUE=URI:https://www.toledowalleye.com/games/2026/11/15/indy-fuel\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:https://www.toledowalleye.com/games/2026/11/21/military-appreciation-ni\r\n"
    " ght\r\n"
    "SUMMARY:Military Appreciation Night\r\n"
    "DTSTART;TZID=America/New_York:20261121T193000\r\n"
    "DESCRIPTION:Rapid City Rush at Toledo Walleye\r\n"
    "LOCATION:Huntington Center\\, Toledo\\, Ohio\r\n"
    "URL;VALUE=URI:https://www.toledowalleye.com/games/2026/11/21/military-appre\r\n"
    " ciation-night\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

GRID = """
<div class="grid grid-cols-7 gap-1">
 <div><time datetime="2026-11-07">7</time><div>
  <a href="https://www.toledowalleye.com/games/2026/11/07/opening-night" title="Opening Night">
   <span>Details</span><div><img alt="Fort Wayne Komets" src="x.png"> FW</div><div>FW</div>
   <div>W 4 - 6</div></a>
  <a href="https://www.toledowalleye.com/events/2026/11/07/block-party" title="Block Party">
   <span>Details</span> Event Block Party 4:00 PM ET</a>
 </div></div>
 <div><time datetime="2026-11-14">14</time><div>
  <a href="https://www.toledowalleye.com/games/2026/11/14/fort-wayne-komets" title="Toledo Walleye @ Fort Wayne Komets">
   <span>Details</span><div>@ <img alt="Fort Wayne Komets" src="x.png"> FW</div><div>FW</div>
   <div>OTL 2 - 3</div></a>
 </div></div>
 <div><time datetime="2026-11-15">15</time><div>
  <a href="https://www.toledowalleye.com/games/2026/11/15/indy-fuel" title="Toledo Walleye @ Indy Fuel">
   <span>Details</span><div>@ <img alt="Indy Fuel" src="x.png"> IND</div> 4:00 PM ET</a>
 </div></div>
</div>
"""


def local(e):
    return e.start.astimezone(TZ)


def test_sidearm_played_away_game():
    e = sidearm.parse_game(PLAYED, ADRIAN)
    check("away game reads '@'", e.title == "🏒 Adrian Hockey @ Lake Forest College", e.title)
    check("score is from Adrian's side", e.result == "W 3–0", str(e.result))
    check("local kickoff kept", local(e).strftime("%Y-%m-%d %H:%M") == "2026-11-14 17:00")
    check("links to the recap", e.url == "https://adrianbulldogs.com/news/2026/11/14/recap.aspx", e.url)
    check("subtitle names league and conference game",
          e.subtitle == "NCAA D-III · Conference", str(e.subtitle))
    check("has a clock time", e.all_day is False)


def test_sidearm_upcoming_home_game():
    e = sidearm.parse_game(UPCOMING_HOME, ADRIAN)
    check("home game reads 'vs'", e.title == "🏒 Adrian Hockey vs St. Norbert College", e.title)
    check("no result before it is played", e.result is None)
    check("venue includes the rink", e.location == "Arrington Ice Arena, Adrian, Mich.", str(e.location))
    check("falls back to the schedule page", e.url == ADRIAN["url"], e.url)


def test_sidearm_unscheduled_time_is_all_day():
    e = sidearm.parse_game(NO_TIME, ADRIAN)
    check("TBA time is all-day, not 12 AM", e.all_day is True)
    check("bad date is skipped", sidearm.parse_game({"date": "soon"}, ADRIAN) is None)


def test_sidearm_window_spans_months():
    months = sidearm._months_between(date(2026, 11, 27), date(2026, 12, 5))
    check("both months are fetched", months == [date(2026, 11, 1), date(2026, 12, 1)], str(months))
    months = sidearm._months_between(date(2026, 12, 30), date(2027, 1, 6))
    check("year rollover", months == [date(2026, 12, 1), date(2027, 1, 1)], str(months))


def test_walleye_feed():
    away, home = walleye.parse_ics(ICS, WALLEYE)
    check("away game title", away.title == "🏒 Walleye @ Indy Fuel", away.title)
    check("feed time is converted from its zone",
          local(away).strftime("%Y-%m-%d %H:%M") == "2026-11-15 16:00")
    check("escaped commas in the venue are unescaped",
          away.location == "Fishers Event Center, Fishers, Indiana", str(away.location))
    check("home game is titled by matchup, not the promotion",
          home.title == "🏒 Walleye vs Rapid City Rush", home.title)
    check("promotion moves to the subtitle",
          home.subtitle == "ECHL · Military Appreciation Night",
          str(home.subtitle))
    check("folded URL line is rejoined",
          home.url == "https://www.toledowalleye.com/games/2026/11/21/military-appreciation-night",
          home.url)


def test_walleye_results_grid():
    results = walleye.parse_results(GRID, WALLEYE)
    check("only played games, no promo events or future games", len(results) == 2, str(len(results)))
    home, away = results
    check("home win shows Toledo's score first", home.result == "W 6–4", str(home.result))
    check("home game reads 'vs'", home.title == "🏒 Walleye vs Fort Wayne Komets", home.title)
    check("overtime loss keeps its label and order", away.result == "OTL 2–3", str(away.result))
    check("away game reads '@'", away.title == "🏒 Walleye @ Fort Wayne Komets", away.title)
    check("result lands on the right day", local(away).date() == date(2026, 11, 14))
    check("no puck-drop time is invented", away.all_day is True)


def main() -> int:
    for t in (
        test_sidearm_played_away_game,
        test_sidearm_upcoming_home_game,
        test_sidearm_unscheduled_time_is_all_day,
        test_sidearm_window_spans_months,
        test_walleye_feed,
        test_walleye_results_grid,
    ):
        print(t.__name__)
        t()
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
