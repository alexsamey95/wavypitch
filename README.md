# 🎧 Wavy Pitch

Pitch your music to curated Spotify playlists — the free, direct way. Discover user-curated playlists in your genre, find the curator's contact, write a personalised pitch per curator, send at a healthy pace, and track placements through to permanent adds.

Forked from **Wavy Outreach**: same CSV database pattern, same GitHub auto-save, same Apify + Gemini + Claude.ai plumbing. The unit of the database changed from *artist* to *playlist*, pitched once per *curator*.

## Pages

- **Dashboard** — funnel meters (Discovered → Qualified → Contactable → Drafted → Pitched → Replied → Added), permanent-placement count, and "Next up" actions computed from your data.
- **Discover** — keyword-search Spotify via Apify. Drops editorial and dump-bin playlists, sweeps *every* text field (owner name → playlist name → description → sibling "Submit Your Music" playlist) for emails / @handles / submission links, scores Fit · Quality · Reachability, tags cost (free / paid-link). Optional Instagram-bio → email scrape for the handful that need it. Paste playlist URLs you found by hand.
- **Write** — optional Gemini name-cleanup, then pitches written through **Claude.ai for free**: download a batch CSV (one row per curator), paste the built-in prompt into claude.ai with the file attached, import the finished CSV back.
- **Send** — one curator at a time, editable pitch, "Open in Mail" / "Open Instagram" / submission link, daily send cap, 7-day follow-ups.
- **Playlists** — the full editable table. Type contacts in by hand (tagged `manual`, highest confidence), edit anything, ❌ delete, 🗑️ block a curator (all their playlists, forever). Add playlists manually without scraping.
- **Settings** — you (artist, genre, track link, monthly listeners), targeting sliders (saves band, "in your lane" plays band, dump-bin threshold, daily cap), Apify actor, keys, blocked curators, backup/restore, factory reset.

## Deploy on Streamlit Cloud

1. Create a **new GitHub repo** and push these files, keeping the folder structure:
   ```
   app.py
   requirements.txt
   .streamlit/config.toml
   .gitignore
   ```
   Do **not** commit `secrets.toml` — `secrets.toml.example` shows the format only.
2. On [share.streamlit.io](https://share.streamlit.io), **New app** → point at `app.py`.
3. App → **Settings → Secrets**, paste:
   ```toml
   APIFY_API_TOKEN = "..."
   GEMINI_API_KEY  = "..."
   GITHUB_TOKEN    = "github_pat_..."
   GITHUB_REPO     = "yourname/wavypitch"
   ```

## GitHub auto-save (turn this on first)

Streamlit Cloud wipes the app's disk on every reboot. With `GITHUB_TOKEN` + `GITHUB_REPO` set, the app **pushes every changed data file to an `app-data` branch after every single interaction** (each click, edit, scrape, import) and restores them on boot — so nothing is ever lost. The data branch is separate from `main`, so saves never trigger a redeploy.

Token: GitHub → Settings → Developer settings → **Fine-grained tokens** → new token → Repository access: *only this repo* → Permissions → **Contents: Read and write**.

The sidebar shows `☁️ All changes saved to GitHub · HH:MM`, a pending count, or an error. There's also a manual **Save to cloud now** button, and a ZIP backup in Settings as belt-and-braces.

## Apify actor — one actor, hardwired

The app is hardwired to the **"Spotify Playlists"** actor (search + full details + added-dates). Settings → Apify actor → paste its slug (`OWNER/NAME` from `apify.com/OWNER/NAME`) or the ID from `console.apify.com/actors/<ID>`. That's the only configuration.

Why this one: it's the only actor found whose keyword search (`terms`) returns, with `expand` on, **followers, track count, and a tracklist with both `plays` and `addedAt`** — so contacts, saves, Quality, Reachability *and real Freshness* are all filled on the first run, with input keys documented as real JSON (`terms / startUrls / maxItems / maxTracks / expand / proxyConfiguration`). No guessing.

- **Tracks per playlist** (default 200): Freshness = newest added-date among fetched tracks, and new adds usually sit at the *end* of a playlist, so fetch enough to reach it. Dump-bins over your threshold are skipped anyway.
- **Proxy**: the actor defaults to no proxy (it uses Spotify's API). Leave off unless runs get blocked — proxy bandwidth is billed extra.
- **Spend cap**: off by default (Settings). If set, Apify aborts the run at that spend.

The row mapper stays tolerant to other actors' field names, but the *input* is this actor's, exactly.

## Cost (realistic)

| API | Job | ≈ per month |
|---|---|---|
| Apify “Spotify Playlists” actor | discovery, one pass with full details | check its Pricing tab; no proxy by default |
| Apify Instagram scraper | bio → email, gated | $0.50–1.50 |
| Gemini Flash | name cleanup | pennies |
| Claude.ai (free batch loop) | writing pitches | $0 |

Runs inside Apify's $5/month free credit at low volume. Compare: SubmitHub/Groover charge $1–2 *per submission* and pull your track after ~a month; direct placements are free and permanent.

## Google Search

Deliberately **not** included. It was the weakest, most expensive step. When the automated sweep comes up empty, the playlist lands in the **Needs manual look** bucket in Playlists — do the professional-stalker work yourself and type the contact in.
