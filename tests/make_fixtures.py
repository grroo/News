#!/usr/bin/env python3
"""
Generate offline fixture feeds for every URL in config.yml so build.py can be
tested without network access:   python tests/make_fixtures.py && \
    python scripts/build.py --mock --fixtures tests/fixtures

Content is synthetic (clearly labelled), but the XML shapes match the real
feeds: RSS 2.0 (BBC/Guardian/Gazzetta), Google News RSS, Yahoo ticker RSS,
YouTube Atom with yt:videoId, podcast RSS with itunes:duration, and the Yahoo
v8 chart JSON.
"""
import html
import argparse
import json
import random
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--output-dir", type=Path, default=ROOT / "tests" / "fixtures")
OUT = ap.parse_args().output_dir
OUT.mkdir(parents=True, exist_ok=True)
cfg = yaml.safe_load((ROOT / "config.yml").read_text())
random.seed(7)
NOW = datetime(2026, 9, 4, 17, 0, tzinfo=timezone.utc)

HEADLINES = {
    "news": ["EU leaders agree outline of new fiscal framework", "Ceasefire talks resume after weekend strikes",
             "Italy unveils 2027 budget priorities", "Tech giants face fresh antitrust probe in Brussels",
             "Major flooding disrupts northern rail network", "ECB officials signal caution on rate path",
             "Milan unveils plan for new metro line extension", "AI model release sparks copyright dispute",
             "Insurers warn on rising catastrophe losses", "Reinsurance renewals point to softer pricing"],
    "sport": ["Inter edge past Juventus in Derby d'Italia", "Inzaghi confirms defender out for three weeks",
              "Italy squad named for October qualifiers", "Ferrari upgrade package arrives for Monza",
              "Serie A weekend review: five things we learned", "Transfer window closes with late loan deals",
              "Champions League draw: Inter land tough group", "Leclerc fastest in second practice"],
    "finance": ["European insurers rally as bond yields fall", "Zurich reports strong H1 combined ratio",
                "Allianz raises full-year outlook", "Generali completes asset management deal",
                "Munich Re flags hurricane season exposure", "Euro Stoxx 50 closes at two-month high",
                "S&P 500 slips as tech leads decline", "EUR/USD steady ahead of payrolls",
                "Bitcoin drifts below key level", "Credit spreads tighten on calmer risk sentiment"],
}
mapping = {}
counter = [0]


def slug(url):
    counter[0] += 1
    return f"f{counter[0]:03d}.xml"


def rss(name, url, titles, hours_ago=None, itunes=False):
    items = []
    for i, t in enumerate(titles):
        dt = NOW - timedelta(hours=(hours_ago[i] if hours_ago else random.uniform(0.5, 30)))
        link = f"https://example.org/{urllib.parse.quote(name.lower().replace(' ', '-'))}/{i}?utm_source=rss"
        extra = f"<itunes:duration>{random.randint(20, 90)}:{random.randint(10, 59):02d}</itunes:duration>" if itunes else ""
        items.append(f"""<item><title>{html.escape(t)} - {html.escape(name)}</title><link>{link}</link><guid>{link}</guid>
<pubDate>{format_datetime(dt)}</pubDate><description>&lt;p&gt;Synthetic fixture summary for &quot;{html.escape(t)}&quot;. Lorem ipsum dolor sit amet, consectetur adipiscing elit.&lt;/p&gt;</description>{extra}</item>""")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>{html.escape(name)}</title>{''.join(items)}</channel></rss>"""
    f = slug(url)
    (OUT / f).write_text(xml)
    mapping[url] = f


def atom_youtube(name, channel_id):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    entries = []
    for i in range(4):
        dt = NOW - timedelta(hours=random.uniform(2, 120))
        vid = f"vid{channel_id[-4:]}{i}"
        entries.append(f"""<entry><id>yt:video:{vid}</id><yt:videoId>{vid}</yt:videoId>
<title>{name} video #{i}: a synthetic fixture</title><link rel="alternate" href="https://www.youtube.com/watch?v={vid}"/>
<published>{dt.isoformat()}</published><media:group><media:description>Fixture description.</media:description></media:group></entry>""")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
<title>{name}</title>{''.join(entries)}</feed>"""
    f = slug(url)
    (OUT / f).write_text(xml)
    mapping[url] = f


def chart_json(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d"
    base = random.uniform(20, 5000)
    closes = [base * (1 + random.uniform(-0.02, 0.02)) for _ in range(5)]
    j = {"chart": {"result": [{"meta": {"currency": "EUR" if symbol.endswith((".SW", ".DE", ".MI")) or symbol.startswith("^STOXX") else "USD"},
                               "indicators": {"quote": [{"close": closes}]}}]}}
    f = f"c{counter[0]:03d}.json"
    counter[0] += 1
    (OUT / f).write_text(json.dumps(j))
    mapping[url] = f


sys.path.insert(0, str(ROOT / "scripts"))
from build import topic_sources  # noqa: E402


for s in cfg["news_sources"]:
    rss(s["name"], s["url"], random.sample(HEADLINES["news"], 5))
for src in topic_sources(cfg["watched_topics"], "Topic"):
    rss(src["name"], src["url"], [f"{h} ({src['name']})" for h in random.sample(HEADLINES["news"], 3)])
for src in topic_sources(cfg["sport_teams"], "Team"):
    rss(src["name"], src["url"], random.sample(HEADLINES["sport"], 4))
for s in cfg["sport_sites"]:
    rss(s["name"], s["url"], random.sample(HEADLINES["sport"], 5))
for t in cfg["tickers"]:
    sym = urllib.parse.quote(t["symbol"])
    rss(t.get("label", t["symbol"]), f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US",
        random.sample(HEADLINES["finance"], 3))
    chart_json(t["symbol"])
for s in cfg["market_news_sources"]:
    rss(s["name"], s["url"], random.sample(HEADLINES["finance"], 5))
for c in cfg["youtube_channels"]:
    atom_youtube(c["name"], c["channel_id"])
for p in cfg["podcasts"]:
    rss(p["name"], p["rss_url"], [f"Episode {n}: fixture episode" for n in range(310, 314)],
        hours_ago=[3, 27, 51, 200], itunes=True)

(OUT / "map.json").write_text(json.dumps(mapping, indent=1))
print(f"wrote {len(mapping)} fixtures to {OUT}", file=sys.stderr)
