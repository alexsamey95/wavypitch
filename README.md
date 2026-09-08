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

## Data source — Spotify Web API (free) by default

Playlist discovery uses Spotify's **official Web API** — free, no scraping, no proxies, paced by the app so a run can't run away. It returns description, owner, followers, track count, and per-track **added-dates** (freshness) and **popularity** (artist size). Spotify-owned editorial playlists aren't visible to it — the app skips those anyway.

**Setup (5 minutes, free):**
1. Go to https://developer.spotify.com/dashboard → Log in → **Create app**.
2. App name: anything (e.g. `wavy-pitch`). Redirect URI: `http://localhost:8501/` (required by the form, never used). API: Web API. Save.
3. Open the app → **Settings** → copy **Client ID** and **Client secret**.
4. Streamlit Cloud → your app → Settings → Secrets → add:
   ```toml
   SPOTIFY_CLIENT_ID     = "..."
   SPOTIFY_CLIENT_SECRET = "..."
   ```

**Flow:** Search (fast — no tracks; activity estimated from text) → Discover step 3 **Enrich** (free — one call per *contactable* playlist fills saves, popularity → Reachability, added-dates → Freshness).

**Optional: Apify actor instead.** Settings → Data source → *Apify actor*. Supports `augeas/spotify-playlists` (rental — monthly fee, not covered by free credit; returns play counts + added-dates) and the ScrapeArchitect Spotify Playlist Scraper (pay-per-result). Runs get a hard time limit, a live Stop button and a runaway guard.

**Instagram** (bio → email) still uses `apify/instagram-profile-scraper` — Instagram blocks self-built scrapers; this one is pay-per-result on free credit and only runs on playlists that have an @handle but no email.

## Cost (realistic)

| API | Job | ≈ per month |
|---|---|---|
| Spotify Web API | discovery + enrichment | **$0** |
| Apify Instagram scraper | bio → email, gated | $0.50–1.50 |
| Gemini Flash | name cleanup | pennies |
| Claude.ai (free batch loop) | writing pitches | $0 |

Runs inside Apify's $5/month free credit at low volume. Compare: SubmitHub/Groover charge $1–2 *per submission* and pull your track after ~a month; direct placements are free and permanent.

## Google Search

Deliberately **not** included. It was the weakest, most expensive step. When the automated sweep comes up empty, the playlist lands in the **Needs manual look** bucket in Playlists — do the professional-stalker work yourself and type the contact in.
