#!/usr/bin/env python3
"""
Find a podcast's public RSS feed from its name, via the iTunes Search API.

    python scripts/find_podcast_rss.py "Huberman Lab" "FT News Briefing"
    python scripts/find_podcast_rss.py --all "Daily"        # show all matches, not just the top one

Prints a ready-to-paste YAML snippet for the `podcasts:` list in config.yml.
Works for any show that is listed on Apple Podcasts (which is nearly every
show also on Spotify — Spotify itself does not expose RSS).
"""
import sys
import urllib.parse

import requests

API = "https://itunes.apple.com/search"


def search(term: str, limit: int = 5) -> list[dict]:
    r = requests.get(API, params={"term": term, "media": "podcast", "entity": "podcast", "limit": limit}, timeout=20)
    r.raise_for_status()
    return [x for x in r.json().get("results", []) if x.get("feedUrl")]


def main():
    args = [a for a in sys.argv[1:] if a != "--all"]
    show_all = "--all" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)
    print("podcasts:")
    for term in args:
        try:
            results = search(term)
        except Exception as e:
            print(f"  # {term}: {e}", file=sys.stderr)
            continue
        if not results:
            print(f"  # {term}: no match on Apple Podcasts", file=sys.stderr)
            continue
        for res in results if show_all else results[:1]:
            name = res["collectionName"].replace('"', "'")
            note = f"  # by {res.get('artistName', '?')}" if show_all else ""
            print(f'  - name: "{name}"{note}\n    rss_url: {res["feedUrl"]}')


if __name__ == "__main__":
    main()
