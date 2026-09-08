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

## Apify actor — use a DEDICATED playlist scraper

Settings → Apify actor → paste the slug of a **Spotify playlist** actor (e.g. `scrapearchitect/spotify-playlist-scraper` — copy the exact slug from its Apify store URL, `apify.com/OWNER/ACTOR`). Style `playlist-scraper`.

**Why not the all-in-one actor:** its keyword search returns ~6 entity types (artists, albums, tracks, playlists, shows, episodes) per keyword and bills every one — we only keep playlists, so ~5/6 of the spend is thrown away. A dedicated playlist actor returns only playlists. Style `all-in-one` is still supported for URL-mode detail fetches but is warned against for search.

**Fast mode by default:** search runs with `fetchDetails=false`, returning `playlist_description` + `playlist_owner` + `owner_url` — the contact fields — at the cheapest tier. Saves + tracklists are fetched later (Discover → step 3) **only for contactable playlists**, so credits never go to dead leads.

**Hard cap:** every run is started with `max_total_charge_usd` (default $1.00, Settings) — Apify aborts the run at the cap. Residential proxy bandwidth (required for Spotify) is billed by Apify on top of per-result pricing, so the cap is your real safety net. **After your first run, open Discover → "Raw sample"** and confirm the actor returned `description`, `followers`, `owner`, and a tracklist with `playcount` — those power the contact sweep and the Reachability score. If a field is missing, the mapper degrades gracefully (you'll just see `Unknown` reachability or fewer auto-found contacts) rather than crashing.

## Cost (realistic)

| API | Job | ≈ per month |
|---|---|---|
| Apify Spotify playlist actor | discovery (fast mode) | varies by actor + proxy usage — set the hard cap |
| Apify Instagram scraper | bio → email, gated | $0.50–1.50 |
| Gemini Flash | name cleanup | pennies |
| Claude.ai (free batch loop) | writing pitches | $0 |

Runs inside Apify's $5/month free credit at low volume. Compare: SubmitHub/Groover charge $1–2 *per submission* and pull your track after ~a month; direct placements are free and permanent.

## Google Search

Deliberately **not** included. It was the weakest, most expensive step. When the automated sweep comes up empty, the playlist lands in the **Needs manual look** bucket in Playlists — do the professional-stalker work yourself and type the contact in.
