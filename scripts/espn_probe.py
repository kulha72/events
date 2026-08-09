#!/usr/bin/env python3
"""
Diagnostic: which ESPN requests get through from a GitHub Actions runner?

Every ESPN endpoint started returning 403 on 2026-08-05, emptying the sports,
playoff and standings feeds. Two candidate causes:

  1. ESPN rejects the bot-style User-Agent the digest used to send.
  2. ESPN blocks GitHub Actions' datacenter IP ranges outright.

This tries the same URLs under several header sets. If the browser headers
succeed, it is (1) and the header change is the fix. If everything 403s
regardless of headers, it is (2) and we need a different data source.

Run: python scripts/espn_probe.py
"""

import sys

import requests

OLD_UA = "Mozilla/5.0 (compatible; daily-digest-espn/1.0)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

HEADER_SETS = {
    "1. no headers at all": {},
    "2. old digest UA": {"User-Agent": OLD_UA},
    "3. browser UA only": {"User-Agent": BROWSER_UA},
    "4. browser UA + accept + referer": {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.espn.com/",
        "Origin": "https://www.espn.com",
        "Connection": "keep-alive",
    },
}

URLS = {
    "team schedule (Tigers)":
        "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/6/schedule",
    "scoreboard (MLB)":
        "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
    "standings (MLB)":
        "https://site.api.espn.com/apis/v2/sports/baseball/mlb/standings?level=3",
    "espn.com homepage":
        "https://www.espn.com/",
}


def main() -> int:
    any_success = False
    header_wins: dict[str, int] = {}

    for label, headers in HEADER_SETS.items():
        print(f"\n=== {label}")
        for name, url in URLS.items():
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                status = resp.status_code
                detail = f"{status} ({len(resp.content)} bytes)"
                if status == 200:
                    any_success = True
                    header_wins[label] = header_wins.get(label, 0) + 1
                elif status == 403:
                    # A CDN block page usually names the vendor — worth seeing.
                    snippet = resp.text[:160].replace("\n", " ").strip()
                    detail += f"  body: {snippet}"
                print(f"  {name:<26} {detail}")
            except Exception as exc:
                print(f"  {name:<26} ERROR {exc}")

    print("\n" + "=" * 60)
    if not any_success:
        print("VERDICT: every header set failed — this looks IP-based, not UA-based.")
        print("         Header changes will not fix it; we need another data source.")
    else:
        print("VERDICT: some requests succeeded. Working header sets:")
        for label, wins in sorted(header_wins.items(), key=lambda kv: -kv[1]):
            print(f"  {label}  ({wins}/{len(URLS)} endpoints OK)")
    print("=" * 60)

    # Always exit 0 — this is a diagnostic, a 403 is a result, not a job failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
