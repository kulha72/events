"""
Run this to see exactly what ESPN returns for the playoffs scoreboard.
Usage:  python3 debug_espn_playoffs.py
"""
import json
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
from collectors.sports.espn import _fetch_json, _scoreboard_url

today = date.today()
yesterday = today - timedelta(days=1)
tomorrow = today + timedelta(days=1)

leagues = ["nba", "nhl"]

for league in leagues:
    url = _scoreboard_url(league)
    print(f"\n{'='*60}")
    print(f"LEAGUE: {league.upper()}")
    print(f"URL: {url}")

    for check_date in [yesterday, today, tomorrow]:
        date_str = check_date.strftime("%Y%m%d")
        print(f"\n  --- {check_date} (seasontype=3) ---")
        try:
            data = _fetch_json(url, params={"dates": date_str, "seasontype": "3"})

            # Season type detection
            season_type_raw = data.get("season", {}).get("type")
            print(f"  data.season: {data.get('season', 'MISSING')}")

            leagues_data = data.get("leagues", [])
            if leagues_data:
                lg_season = leagues_data[0].get("season", {})
                print(f"  leagues[0].season: {lg_season}")
                if season_type_raw is None:
                    season_type_raw = lg_season.get("type")

            print(f"  season_type_raw (resolved): {repr(season_type_raw)}")

            events = data.get("events", [])
            print(f"  events count: {len(events)}")

            for i, ev in enumerate(events[:3]):
                print(f"\n  Event[{i}]:")
                print(f"    id: {ev.get('id')}")
                print(f"    name: {ev.get('name')}")
                print(f"    season: {ev.get('season', 'MISSING')}")
                comp = ev.get("competitions", [{}])[0]
                print(f"    competition.date: {comp.get('date')}")
                notes = comp.get("notes", [])
                print(f"    notes: {notes}")
                competitors = comp.get("competitors", [])
                for c in competitors:
                    print(f"    competitor: {c.get('team', {}).get('displayName')} ({c.get('homeAway')}) score={c.get('score')}")

        except Exception as e:
            print(f"  ERROR: {e}")

    # Also try without seasontype to compare
    print(f"\n  --- {today} (NO seasontype param) ---")
    try:
        data = _fetch_json(url, params={"dates": today.strftime("%Y%m%d")})
        print(f"  data.season: {data.get('season', 'MISSING')}")
        leagues_data = data.get("leagues", [])
        if leagues_data:
            print(f"  leagues[0].season: {leagues_data[0].get('season', 'MISSING')}")
        events = data.get("events", [])
        print(f"  events count: {len(events)}")
        if events:
            print(f"  First event: {events[0].get('name')} | season: {events[0].get('season')}")
    except Exception as e:
        print(f"  ERROR: {e}")
