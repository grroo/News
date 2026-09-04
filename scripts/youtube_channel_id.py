#!/usr/bin/env python3
"""
Turn a YouTube channel URL (or @handle) into its channel_id for config.yml.

    python scripts/youtube_channel_id.py https://www.youtube.com/@veritasium
    python scripts/youtube_channel_id.py @veritasium https://youtube.com/c/Bloomberg
    python scripts/youtube_channel_id.py https://www.youtube.com/watch?v=dQw4w9WgXcQ   # video → its channel

Prints a ready-to-paste YAML snippet. Uses only the public channel page, no API key.
"""
import html
import re
import sys
import urllib.parse

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def normalise(arg: str) -> str:
    arg = arg.strip()
    if arg.startswith("@"):
        return f"https://www.youtube.com/{arg}"
    if re.fullmatch(r"UC[\w-]{22}", arg):
        return f"https://www.youtube.com/channel/{arg}"
    if not arg.startswith("http"):
        arg = "https://" + arg
    return arg


def resolve(url: str) -> tuple[str, str]:
    m = re.search(r"/channel/(UC[\w-]{22})", url)
    if m:
        cid = m.group(1)
    else:
        cid = None
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=20, cookies={"CONSENT": "YES+1"})
    r.raise_for_status()
    page = r.text
    if not cid:
        for pat in (r'"channelId":"(UC[\w-]{22})"', r'<meta itemprop="channelId" content="(UC[\w-]{22})"',
                    r'"externalId":"(UC[\w-]{22})"', r'channel_id=(UC[\w-]{22})'):
            m = re.search(pat, page)
            if m:
                cid = m.group(1)
                break
    if not cid:
        raise SystemExit(f"Could not find a channel id on {url}")
    name = "?"
    m = re.search(r'<meta property="og:title" content="([^"]+)"', page) or re.search(r"<title>([^<]+)</title>", page)
    if m:
        name = html.unescape(m.group(1)).replace(" - YouTube", "").strip()
    return name, cid


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    print("youtube_channels:")
    for arg in sys.argv[1:]:
        try:
            name, cid = resolve(normalise(arg))
            print(f"  - name: {name}\n    channel_id: {cid}")
        except Exception as e:
            print(f"  # {arg}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
