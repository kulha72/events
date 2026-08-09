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
import time

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


# Seconds to wait between requests. The first run fired ~16 requests in about a
# second, which left the result confounded: the only header set that worked was
# also the one that ran first, so a rate limiter would explain it just as well
# as the headers would. Spacing the requests out removes that explanation.
DELAY = 5

# Order matters for the same reason — pass "reverse" to run the sets in the
# opposite order. If the same set wins both ways, it is the headers, not the
# ordering.
def main() -> int:
    order = list(HEADER_SETS)
    if "reverse" in sys.argv:
        order.reverse()
    print(f"Order: {' -> '.join(order)}    delay between requests: {DELAY}s")

    wins: dict[str, int] = {label: 0 for label in order}
    # Only the API host counts — the homepage is context, not a feed we use.
    api_urls = {k: v for k, v in URLS.items() if "site.api.espn.com" in v}

    for label in order:
        headers = HEADER_SETS[label]
        print(f"\n=== {label}")
        for name, url in URLS.items():
            time.sleep(DELAY)
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                status = resp.status_code
                detail = f"{status} ({len(resp.content)} bytes)"
                if status == 200 and name in api_urls:
                    wins[label] += 1
                elif status == 403:
                    # A CDN block page usually names the vendor — worth seeing.
                    snippet = resp.text[:160].replace("\n", " ").strip()
                    detail += f"  body: {snippet}"
                print(f"  {name:<26} {detail}", flush=True)
            except Exception as exc:
                print(f"  {name:<26} ERROR {exc}", flush=True)

    print("\n" + "=" * 60)
    print(f"API endpoints returning 200, out of {len(api_urls)} per header set:")
    for label in order:
        print(f"  {wins[label]}/{len(api_urls)}  {label}")
    if not any(wins.values()):
        print("\nVERDICT: nothing got through — the block is on the IP range.")
    elif all(wins.values()):
        print("\nVERDICT: everything got through — the earlier 403s were rate limiting.")
    else:
        print("\nVERDICT: header-dependent. The winning set above is what the collector should send.")
    print("=" * 60)

    # Always exit 0 — this is a diagnostic, a 403 is a result, not a job failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
