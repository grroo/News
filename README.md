# Personal briefing

A static, phone-first news briefing that updates three times a day (07:00, 13:00, 19:00 Europe/Rome) and replaces doom-scrolling. Four squares — **News · Sport · Finance · Media** — each with a short LLM-written briefing and a handful of selected items linking to the originals.

- No recommendation algorithm. Selection is done by Claude reading the plain-text `interests` you write in `config.yml`.
- Only public RSS/Atom feeds and public APIs. Nothing behind paywalls or logins.
- No server, no database. GitHub Actions builds `data/briefing.json`; GitHub Pages serves it. Read state lives in your browser's localStorage.
- One config file. You never touch code to change what it covers.

```
config.yml                     ← everything you edit
scripts/build.py               ← fetch → dedupe → Claude (1 call per section) → data/briefing.json
scripts/feeds.py               ← tiny stdlib RSS/Atom parser
scripts/youtube_channel_id.py  ← channel URL → channel_id
scripts/find_podcast_rss.py    ← show name → RSS url
site/index.html                ← the whole site (no framework, no build step)
data/briefing.json             ← current briefing (+ data/past/ keeps the last 6)
.github/workflows/build.yml    ← cron + manual trigger; commits data/, deploys Pages
tests/                         ← offline fixtures for a network-free test run
```

## Deploy in 10 minutes

1. **Create the repo.** Push this folder to a new GitHub repository (public or private — Pages works on private repos with GitHub Pro/Team; on a free account use a public repo). Default branch must be `main`.

2. **Add the API key secret.** Repo → *Settings → Secrets and variables → Actions → New repository secret*:
   - Name: `ANTHROPIC_API_KEY`
   - Value: your key from https://console.anthropic.com

   (Without the secret the workflow still runs and publishes a *mock* briefing — newest items, no LLM text — so you can check the site before spending anything.)

3. **Turn on Pages.** Repo → *Settings → Pages → Build and deployment → Source: **GitHub Actions***. That's it — no branch to pick.

4. **Allow the workflow to commit.** Repo → *Settings → Actions → General → Workflow permissions → **Read and write permissions*** → Save. (Needed so the bot can commit `data/briefing.json` back.)

5. **Run it once.** Repo → *Actions → "Build briefing & deploy" → Run workflow*. About a minute later the site is live at `https://<your-user>.github.io/<repo>/`. Add it to your phone's home screen (Safari: Share → Add to Home Screen) — it has a dark theme-colour and safe-area padding, so it feels like an app.

From then on it runs on its own at 07:00, 13:00 and 19:00 Rome time, and also whenever you push a change to `config.yml`.

> GitHub's cron is best-effort: scheduled runs can start 5–15 minutes late during busy periods, and on repos with no commits for 60 days GitHub pauses schedules (a push or a manual run re-enables them).

## Editing what it covers

Everything is in `config.yml`. The comments there explain each list. The two tedious bits have helpers:

```bash
pip install -r requirements.txt

# YouTube channel URL / @handle / video URL  →  channel_id
python scripts/youtube_channel_id.py https://www.youtube.com/@veritasium @lexfridman

# Podcast name  →  public RSS (via the iTunes Search API; add --all to see several matches)
python scripts/find_podcast_rss.py "Huberman Lab" "FT News Briefing"
```

Both print YAML you paste straight into the `youtube_channels:` / `podcasts:` lists. Spotify does not expose RSS, but practically every show on Spotify is also on Apple Podcasts, which is what the finder searches.

The `interests:` block is the whole "algorithm". Write it like a note to a smart assistant: what you care about, what to skip, how to order things. Push, and the next run uses it.

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # or omit → mock mode
python scripts/build.py                  # writes data/briefing.json
python scripts/build.py --mock           # no LLM, newest items
cd site && python -m http.server 8000    # then open http://localhost:8000
```

The site fetches `data/briefing.json` relative to itself, so for a local preview either symlink `site/data → ../data` or copy the `data/` folder into `site/` (the workflow copies it at deploy time).

Fully offline test (synthetic feeds, no network):

```bash
python tests/make_fixtures.py
python scripts/build.py --mock --fixtures tests/fixtures
```

## How the build works

1. Fetches every feed in `config.yml` concurrently (watched topics and teams become Google News RSS queries; tickers become Yahoo Finance per-ticker RSS; YouTube channels use the public Atom feed).
2. Keeps items from the last `lookback_hours` (media: `media_days`), dedupes by canonical URL and by normalised headline (so the same story from two outlets collapses), and marks items as **new** if they were not in `data/seen.json` from a previous run.
3. Fetches day-change prices for each ticker (`yfinance`, falling back to Yahoo's chart endpoint).
4. Sends each of News / Sport / Finance to Claude **once**, with up to `max_candidates` items (title, source, time, ≤160-char summary) plus your `interests`, and asks for strict JSON: a 4–6 sentence briefing and the selected items with one-line summaries. Any item whose URL is not in the candidates is dropped — the model cannot invent stories. If the API call fails, that section falls back to "newest first" and says so.
5. Media is not summarised: newest videos and episodes from the last 3 days, with duration when the feed provides it.
6. Rotates the previous `briefing.json` into `data/past/` (keeps 6), writes the new one, and the workflow commits `data/` and deploys `site/ + data/` to Pages.

## Cost

Three runs a day × three LLM calls = 9 calls/day. With 40 candidates per section a call is roughly 3.5–4k input tokens and ~600 output tokens.

| model in `config.yml` | ≈ per call | ≈ per month |
|---|---|---|
| `claude-sonnet-4-6` (default) | $0.02 | **$5–6** |
| `claude-haiku-4-5` | $0.007 | **$1.5–2** |

To get under €1/month: switch `model` to Haiku **and** lower `max_candidates` to ~25, or drop to two runs a day (edit the hours in the workflow's "Is it time?" step). GitHub Actions minutes and Pages are free for this volume. Check actual pricing at https://www.anthropic.com/pricing — it changes.

## Privacy / data

The site stores only two things in your browser: which item keys you have opened (`briefing.seen.v1`) and your light/dark override. Nothing is sent anywhere. Your `interests` text goes to the Anthropic API in each call and to nowhere else; it is committed in `config.yml`, so keep the repo private if you'd rather not publish it.
