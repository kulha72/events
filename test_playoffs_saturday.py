"""
Test that playoff games on a Saturday are not dropped when the ESPN
scoreboard response omits the season.type field.
"""

import sys
import os
from datetime import date, datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collectors.sports.espn import ESPNCollector

CONFIG = {
    "sports": {
        "teams": [],
        "tours": [],
        "playoffs": [
            {"league": "nba"},
            {"league": "nhl"},
        ],
    }
}

# A Saturday within the lookahead window (today=Friday 2026-04-17, saturday=2026-04-18)
TODAY = date(2026, 4, 17)
SATURDAY = date(2026, 4, 18)


def _make_game(game_id: str, home: str, away: str, game_date: date) -> dict:
    dt = datetime(game_date.year, game_date.month, game_date.day, 19, 0, tzinfo=timezone.utc)
    return {
        "id": game_id,
        "date": dt.isoformat().replace("+00:00", "Z"),
        "competitions": [{
            "date": dt.isoformat().replace("+00:00", "Z"),
            "status": {"type": {"completed": False, "name": "STATUS_SCHEDULED"}},
            "competitors": [
                {"homeAway": "home", "team": {"id": "1", "displayName": home}},
                {"homeAway": "away", "team": {"id": "2", "displayName": away}},
            ],
            "notes": [{"headline": "Game 3"}],
            "venue": {"fullName": "Test Arena"},
            "links": [],
        }],
    }


def _make_response(games: list[dict], include_season_type: bool) -> dict:
    """Build a fake ESPN scoreboard response, optionally omitting season.type."""
    resp: dict = {"events": games}
    if include_season_type:
        resp["season"] = {"type": 3}
    # When include_season_type=False, season.type is absent — the bug scenario
    return resp


def test_saturday_games_with_season_type():
    """Control: season.type=3 present — games should appear (was always working)."""
    sat_game = _make_game("sat1", "Heat", "Celtics", SATURDAY)

    def fake_fetch(url, params=None):
        d = params.get("dates", "")
        if d == SATURDAY.strftime("%Y%m%d"):
            return _make_response([sat_game], include_season_type=True)
        return _make_response([], include_season_type=True)

    collector = ESPNCollector(CONFIG)
    with patch("collectors.sports.espn._fetch_json", side_effect=fake_fetch):
        events = collector.collect_playoffs(TODAY, lookahead_days=7)

    sat_events = [e for e in events if e.start.date() == SATURDAY]
    assert sat_events, "FAIL: Saturday game missing even with season.type=3"
    print(f"  PASS (with season.type): {sat_events[0].title}")


def test_saturday_games_without_season_type():
    """Bug scenario: season.type absent on Saturday — games must NOT be dropped."""
    sat_game = _make_game("sat2", "Rangers", "Hurricanes", SATURDAY)

    def fake_fetch(url, params=None):
        d = params.get("dates", "")
        if d == SATURDAY.strftime("%Y%m%d"):
            return _make_response([sat_game], include_season_type=False)
        # Other days return no games but with proper season type
        return _make_response([], include_season_type=True)

    collector = ESPNCollector(CONFIG)
    with patch("collectors.sports.espn._fetch_json", side_effect=fake_fetch):
        events = collector.collect_playoffs(TODAY, lookahead_days=7)

    sat_events = [e for e in events if e.start.date() == SATURDAY]
    assert sat_events, "FAIL: Saturday game dropped when season.type is missing"
    print(f"  PASS (without season.type): {sat_events[0].title}")


def test_non_postseason_days_still_skipped():
    """Safety: days where ESPN reports season.type != 3 should still be excluded."""
    wrong_season_game = _make_game("reg1", "Lakers", "Warriors", SATURDAY)

    def fake_fetch(url, params=None):
        return {"events": [wrong_season_game], "season": {"type": 2}}  # regular season

    collector = ESPNCollector(CONFIG)
    with patch("collectors.sports.espn._fetch_json", side_effect=fake_fetch):
        events = collector.collect_playoffs(TODAY, lookahead_days=7)

    assert not events, "FAIL: regular-season games should be excluded"
    print("  PASS (regular-season games correctly excluded)")


if __name__ == "__main__":
    print("Running playoff Saturday feed tests...\n")
    tests = [
        test_saturday_games_with_season_type,
        test_saturday_games_without_season_type,
        test_non_postseason_days_still_skipped,
    ]
    failed = 0
    for t in tests:
        print(f"[{t.__name__}]")
        try:
            t()
        except AssertionError as e:
            print(f"  {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1
    print()
    if failed:
        print(f"{failed}/{len(tests)} test(s) FAILED")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed.")
