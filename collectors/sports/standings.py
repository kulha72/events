"""
Standings collector — league tables via the same unofficial ESPN public API
used by the schedule collector. No auth required.

Unlike the other collectors this does not produce Events; it returns a plain
data structure that the static page renders as a Standings section:

    [
        {"key": "mlb", "label": "MLB", "emoji": "⚾",
         "groups": [
             {"name": "AL East",
              "columns": ["W", "L", "PCT", "GB"],
              "rows": [
                  {"rank": None, "team": "New York Yankees", "logo": "https://…",
                   "note_color": None, "note_desc": None,
                   "cells": ["58", "34", ".630", "-"]},
                  …
              ]},
             …
         ]},
        …
    ]

Leagues are configured under sports.standings in config.yaml, e.g.:

    sports:
      standings:
        - league: "mlb"
        - league: "cfb"
          conference_group: 5   # ESPN conference id (5 = Big Ten)
"""

import requests

ESPN_STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports"
CFB_RANKINGS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/rankings"

# Map config league slug -> (sport_path, league_path)
STANDINGS_LEAGUE_MAP = {
    "nfl":        ("football",   "nfl"),
    "mlb":        ("baseball",   "mlb"),
    "nba":        ("basketball", "nba"),
    "nhl":        ("hockey",     "nhl"),
    "cfb":        ("football",   "college-football"),
    "ncaaf":      ("football",   "college-football"),
    "ncaamb":     ("basketball", "mens-college-basketball"),
    "epl":        ("soccer",     "eng.1"),
    "laliga":     ("soccer",     "esp.1"),
    "bundesliga": ("soccer",     "ger.1"),
    "seriea":     ("soccer",     "ita.1"),
    "ligue1":     ("soccer",     "fra.1"),
    "mls":        ("soccer",     "usa.1"),
    "liga_betplay": ("soccer",   "col.1"),
}

LEAGUE_LABEL = {
    "nfl":        "NFL",
    "mlb":        "MLB",
    "nba":        "NBA",
    "nhl":        "NHL",
    "cfb":        "CFB",
    "ncaaf":      "CFB",
    "ncaamb":     "CBB",
    "epl":        "Premier League",
    "laliga":     "La Liga",
    "bundesliga": "Bundesliga",
    "seriea":     "Serie A",
    "ligue1":     "Ligue 1",
    "mls":        "MLS",
    "liga_betplay": "Liga BetPlay",
}

LEAGUE_EMOJI = {
    "nfl": "🏈", "mlb": "⚾", "nba": "🏀", "nhl": "🏒",
    "cfb": "🏈", "ncaaf": "🏈", "ncaamb": "🏀",
    "epl": "⚽", "laliga": "⚽", "bundesliga": "⚽", "seriea": "⚽",
    "ligue1": "⚽", "mls": "⚽", "liga_betplay": "⚽",
}

# Columns per league: (header, [candidate ESPN stat names, lowercase]).
# ESPN's stat naming drifts between sports, so each column lists every
# name observed for it; the first one present in the entry wins.
_SOCCER_COLUMNS = [
    ("GP",  ["gamesplayed"]),
    ("W",   ["wins"]),
    ("D",   ["ties", "draws"]),
    ("L",   ["losses"]),
    ("GD",  ["pointdifferential", "goaldifferential", "differential"]),
    ("PTS", ["points"]),
]

LEAGUE_COLUMNS = {
    "mlb": [
        ("W",   ["wins"]),
        ("L",   ["losses"]),
        ("PCT", ["winpercent", "leaguewinpercent"]),
        ("GB",  ["gamesbehind"]),
    ],
    "nfl": [
        ("W",   ["wins"]),
        ("L",   ["losses"]),
        ("T",   ["ties"]),
        ("PCT", ["winpercent", "leaguewinpercent"]),
    ],
    "nba": [
        ("W",   ["wins"]),
        ("L",   ["losses"]),
        ("PCT", ["winpercent", "leaguewinpercent"]),
        ("GB",  ["gamesbehind"]),
    ],
    "nhl": [
        ("GP",  ["gamesplayed"]),
        ("W",   ["wins"]),
        ("L",   ["losses"]),
        ("OTL", ["otlosses", "overtimelosses", "otl"]),
        ("PTS", ["points"]),
    ],
    "cfb": [
        ("CONF", ["vsconf", "vs. conf.", "vsconference"]),
        ("W",    ["wins"]),
        ("L",    ["losses"]),
        ("PCT",  ["winpercent", "leaguewinpercent"]),
    ],
    "epl":        _SOCCER_COLUMNS,
    "laliga":     _SOCCER_COLUMNS,
    "bundesliga": _SOCCER_COLUMNS,
    "seriea":     _SOCCER_COLUMNS,
    "ligue1":     _SOCCER_COLUMNS,
    "mls":        _SOCCER_COLUMNS,
    "liga_betplay": _SOCCER_COLUMNS,
}
LEAGUE_COLUMNS["ncaaf"] = LEAGUE_COLUMNS["cfb"]
LEAGUE_COLUMNS["ncaamb"] = LEAGUE_COLUMNS["cfb"]

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; daily-digest-espn/1.0)"})


def _fetch_json(url: str, params: dict | None = None) -> dict:
    resp = _session.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _stat_lookup(stats: list[dict]) -> dict[str, str]:
    """Index an entry's stats by every identifier ESPN provides (name, type,
    abbreviation), lowercased, so column candidates can match any of them."""
    lookup: dict[str, str] = {}
    for stat in stats:
        display = stat.get("displayValue")
        if display is None:
            value = stat.get("value")
            display = str(value) if value is not None else ""
        for key in (stat.get("name"), stat.get("type"), stat.get("abbreviation")):
            if key:
                lookup.setdefault(str(key).lower(), str(display))
    return lookup


def _parse_entry(entry: dict, columns: list[tuple[str, list[str]]]) -> dict:
    team = entry.get("team", {})
    logos = team.get("logos") or []
    logo = logos[0].get("href") if logos and isinstance(logos[0], dict) else None

    note = entry.get("note") or {}

    lookup = _stat_lookup(entry.get("stats") or [])
    cells = []
    for _, candidates in columns:
        value = next((lookup[c] for c in candidates if c in lookup), "–")
        cells.append(value)

    return {
        "rank": None,
        "team": team.get("displayName") or team.get("shortDisplayName") or team.get("name") or "?",
        "logo": logo,
        "note_color": note.get("color"),
        "note_desc": note.get("description"),
        "cells": cells,
    }


def _walk_groups(node: dict, out: list[dict], columns: list[tuple[str, list[str]]]) -> None:
    """Recursively collect the deepest standings tables in ESPN's
    conference/division tree. A node with children may also carry its own
    aggregate standings — prefer the more granular child tables."""
    children = node.get("children") or []
    if children:
        for child in children:
            _walk_groups(child, out, columns)
        return

    entries = (node.get("standings") or {}).get("entries") or []
    if not entries:
        return

    out.append({
        "name": node.get("name") or node.get("abbreviation") or "",
        "columns": [header for header, _ in columns],
        "rows": [_parse_entry(e, columns) for e in entries],
    })


def _collect_league_standings(league: str, conference_group: int | None = None) -> list[dict]:
    sport, lg = STANDINGS_LEAGUE_MAP[league]
    url = f"{ESPN_STANDINGS_BASE}/{sport}/{lg}/standings"
    params: dict = {"level": 3}
    if conference_group is not None:
        params["group"] = conference_group

    data = _fetch_json(url, params=params)

    columns = LEAGUE_COLUMNS.get(league, [("W", ["wins"]), ("L", ["losses"])])
    groups: list[dict] = []
    _walk_groups(data, groups, columns)
    return groups


def _collect_cfb_top25() -> dict | None:
    """AP Top 25 from the ESPN rankings endpoint — the quick 'standings'
    check for college football alongside conference standings."""
    data = _fetch_json(CFB_RANKINGS_URL)

    rankings = data.get("rankings") or []
    ap = next(
        (r for r in rankings if "ap" in (r.get("shortName") or r.get("name") or "").lower()),
        rankings[0] if rankings else None,
    )
    if not ap:
        return None

    rows = []
    for rank in ap.get("ranks") or []:
        team = rank.get("team", {})
        record = rank.get("recordSummary") or (rank.get("record") or {}).get("summary") or "–"
        trend = rank.get("trend") or ""
        rows.append({
            "rank": rank.get("current"),
            "team": team.get("nickname") or team.get("location") or team.get("displayName") or "?",
            "logo": team.get("logo"),
            "note_color": None,
            "note_desc": None,
            "cells": [record, trend],
        })

    if not rows:
        return None

    return {
        "name": ap.get("name") or "AP Top 25",
        "columns": ["REC", "TREND"],
        "rows": rows,
    }


def collect_standings(config: dict) -> list[dict]:
    """Collect standings for every league listed under sports.standings.
    One failed league never breaks the rest."""
    standings_cfgs = config.get("sports", {}).get("standings", [])
    leagues: list[dict] = []

    for cfg in standings_cfgs:
        league = cfg.get("league", "")
        if league not in STANDINGS_LEAGUE_MAP:
            print(f"  [standings] Warning: unknown league '{league}', skipping")
            continue

        groups: list[dict] = []

        # College football gets the AP Top 25 in addition to conference standings.
        if league in ("cfb", "ncaaf"):
            try:
                top25 = _collect_cfb_top25()
                if top25:
                    groups.append(top25)
            except Exception as e:
                print(f"  [standings] Warning: CFB rankings fetch failed: {e}")

        try:
            groups.extend(_collect_league_standings(league, cfg.get("conference_group")))
        except Exception as e:
            print(f"  [standings] Warning: {league} standings fetch failed: {e}")

        if groups:
            leagues.append({
                "key": league,
                "label": cfg.get("name") or LEAGUE_LABEL.get(league, league.upper()),
                "emoji": LEAGUE_EMOJI.get(league, ""),
                "groups": groups,
            })

    return leagues
