#!/usr/bin/env python3
"""
Tests for source-health classification and the ESPN league slug map.

The bug these cover: every empty source rendered as one undifferentiated
"No events returned: adrian, annarbor, football_data, liquipedia, pandascore,
playoffs, tca, tecumseh" line. Half of those sources have nothing wired to them
and can never return events, one is an out-of-season playoff feed, and the rest
were genuinely broken — but all eight looked identical, so the line carried no
signal. These tests pin down the distinction.

Run: python test_source_health.py
"""

import sys

import errors
from collectors.sports.espn import LEAGUE_MAP, _scoreboard_url
from collectors.sports.api_football import APIFootballCollector
from collectors.sports.football_data import FootballDataCollector
from collectors.esports.liquipedia import LiquipediaCollector
from collectors.esports.pandascore import PandaScoreCollector

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS {label}")
    else:
        print(f"  FAIL {label}{': ' + detail if detail else ''}")
        _failures.append(label)


# ── ESPN league slugs ─────────────────────────────────────────────────────────

def test_conference_league_slug():
    """The spelled-out 'uefa.europa.conference' 400s; ESPN's path is '…conf'."""
    print("\n[test_conference_league_slug]")
    sport, path = LEAGUE_MAP["uefa.europa.conference"]
    check("sport is soccer", sport == "soccer", sport)
    check("league path is uefa.europa.conf", path == "uefa.europa.conf", path)

    url = _scoreboard_url("uefa.europa.conference")
    check(
        "scoreboard URL uses the short slug",
        url.endswith("/soccer/uefa.europa.conf/scoreboard"),
        url,
    )
    check(
        "no request is built against the 400-ing long slug",
        "uefa.europa.conference/scoreboard" not in url,
        url,
    )


def test_config_league_keys_all_resolve():
    """Every extra_league in config must have a LEAGUE_MAP entry, or the
    collector silently skips it with an 'unknown league' warning."""
    print("\n[test_config_league_keys_all_resolve]")
    import yaml

    config = yaml.safe_load(open("config.yaml"))
    missing = []
    for team in config.get("sports", {}).get("teams", []):
        for league in [team.get("league")] + team.get("extra_leagues", []):
            if league and league not in LEAGUE_MAP:
                missing.append(f"{team.get('name')}:{league}")
    check("all configured leagues are mapped", not missing, str(missing))


# ── Dormant sources are not failures ──────────────────────────────────────────

_CONFIG_ALL_ESPN = {
    "sports": {"teams": [{"name": "Arsenal", "league": "epl", "source": "espn"}]},
    "esports": {"games": [{"name": "CS2", "source": "pandascore"}]},
}


def test_unconfigured_sources_report_as_dormant():
    """A source nothing routes to must not report a missing API key as a
    failure — it would return nothing even with a valid key."""
    print("\n[test_unconfigured_sources_report_as_dormant]")

    from datetime import date

    for label, collector in (
        ("api_football", APIFootballCollector(_CONFIG_ALL_ESPN)),
        ("football_data", FootballDataCollector(_CONFIG_ALL_ESPN)),
        ("liquipedia", LiquipediaCollector(_CONFIG_ALL_ESPN)),
    ):
        errors.clear()
        # No API key set, and nothing in config routes here.
        collector.api_key = ""
        events = collector.collect(date.today(), 7)
        summary = errors.summary()
        check(f"{label} returns no events", events == [], str(events))
        check(f"{label} raises no failure", not summary["failures"], str(summary["failures"]))
        check(
            f"{label} is reported as not configured",
            [s["source"] for s in summary["not_configured"]] == [label],
            str(summary["not_configured"]),
        )
        check(
            f"{label} is kept out of the bare empty list",
            label not in summary["empty_sources"],
            str(summary["empty_sources"]),
        )


def test_configured_source_still_reports_missing_key():
    """The dormancy check must not swallow a real missing-credential problem
    for a source that genuinely has work to do."""
    print("\n[test_configured_source_still_reports_missing_key]")
    from datetime import date

    config = {
        "sports": {
            "teams": [
                {
                    "name": "Millonarios",
                    "league": "liga_betplay",
                    "source": "api_football",
                    "api_football_team_id": 1234,
                }
            ]
        }
    }
    errors.clear()
    collector = APIFootballCollector(config)
    collector.api_key = ""
    collector.collect(date.today(), 7)
    summary = errors.summary()
    check(
        "missing key is still a failure when a team is routed here",
        [f["source"] for f in summary["failures"]] == ["api_football"],
        str(summary),
    )
    check("not misfiled as dormant", not summary["not_configured"], str(summary["not_configured"]))


# ── Classification of empty results ───────────────────────────────────────────

def test_summary_separates_the_four_states():
    """failure / suspect / idle / not-configured must not collapse together."""
    print("\n[test_summary_separates_the_four_states]")
    errors.clear()

    errors.record("espn", "scoreboard fetch failed: 400 Bad Request")
    errors.note_count("espn", 0)
    errors.note_suspect("adrian", "fetched 8000 chars but no event item matched")
    errors.note_count("adrian", 0)
    errors.note_idle("playoffs", "no postseason games scheduled (out of season)")
    errors.note_count("playoffs", 0)
    errors.note_not_configured("liquipedia", "no game sets source: liquipedia")
    errors.note_count("liquipedia", 0)
    errors.note_count("tecumseh", 0)  # explained by nobody

    s = errors.summary()
    check("failure stays a failure", [f["source"] for f in s["failures"]] == ["espn"], str(s["failures"]))
    check("suspect is its own bucket", [x["source"] for x in s["suspect"]] == ["adrian"], str(s["suspect"]))
    check("idle is its own bucket", [x["source"] for x in s["idle"]] == ["playoffs"], str(s["idle"]))
    check(
        "not_configured is its own bucket",
        [x["source"] for x in s["not_configured"]] == ["liquipedia"],
        str(s["not_configured"]),
    )
    check(
        "only the unexplained source stays in empty_sources",
        s["empty_sources"] == ["tecumseh"],
        str(s["empty_sources"]),
    )


def test_suspect_outranks_softer_verdicts():
    """A source that reports twice keeps the most alarming verdict."""
    print("\n[test_suspect_outranks_softer_verdicts]")
    errors.clear()
    errors.note_idle("pandascore", "nothing scheduled")
    errors.note_suspect("pandascore", "50 matches returned, league filter matched none")
    s = errors.summary()
    check("suspect wins over idle", [x["source"] for x in s["suspect"]] == ["pandascore"], str(s))
    check("not also listed as idle", not s["idle"], str(s["idle"]))

    errors.clear()
    errors.note_suspect("pandascore", "league filter matched none")
    errors.note_idle("pandascore", "nothing scheduled")
    check("order does not matter", [x["source"] for x in errors.summary()["suspect"]] == ["pandascore"])


def test_failure_supersedes_status():
    """A hard failure hides the softer status for the same source, so one
    source never appears in two buckets at once."""
    print("\n[test_failure_supersedes_status]")
    errors.clear()
    errors.note_suspect("tca", "iframe rendered but no cards matched")
    errors.record("tca", "playwright crashed")
    s = errors.summary()
    check("reported as a failure", [f["source"] for f in s["failures"]] == ["tca"], str(s["failures"]))
    check("not double-reported as suspect", not s["suspect"], str(s["suspect"]))


def test_secrets_are_redacted_in_status_reasons():
    """Status reasons reach the public gh-pages branch, same as failures."""
    print("\n[test_secrets_are_redacted_in_status_reasons]")
    import os

    errors.clear()
    os.environ["PANDASCORE_API_KEY"] = "supersecretvalue123"
    try:
        errors.note_suspect("pandascore", "failed with token=supersecretvalue123 in url")
        reason = errors.summary()["suspect"][0]["reason"]
        check("raw key value is gone", "supersecretvalue123" not in reason, reason)
    finally:
        del os.environ["PANDASCORE_API_KEY"]


if __name__ == "__main__":
    print("Running source-health tests...")
    test_conference_league_slug()
    test_config_league_keys_all_resolve()
    test_unconfigured_sources_report_as_dormant()
    test_configured_source_still_reports_missing_key()
    test_summary_separates_the_four_states()
    test_suspect_outranks_softer_verdicts()
    test_failure_supersedes_status()
    test_secrets_are_redacted_in_status_reasons()

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed: {', '.join(_failures)}")
        sys.exit(1)
    print("All checks passed.")
