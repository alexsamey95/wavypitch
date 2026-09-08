# 🎧 Wavy Pitch — Design Document

*A Spotify playlist-pitching app. Discover curated playlists in your genre, find
the curator's contact, write personalized pitches, and track placements through
to permanent adds. Built as a fork of **Wavy Outreach**, changing the unit of the
database from **artist** to **playlist**.*

---

## 1. The thesis

Playlist pitching *looks* like a scraping problem. It isn't. Spotify exposes
**zero curator emails** — so the actual work is:

1. Find playlists in your genre,
2. Figure out **who runs them and how to reach them**,
3. Qualify **whether an add is realistic for an artist your size**,
4. Pitch, and track through to a permanent placement.

Steps 2–4 are exactly what Wavy Outreach already does for artists. So this is a
**fork**, not a new app. The scrape is the easy part; the value is the
**contact-discovery funnel** and the **targeting filters**.

This is a proven manual method being automated. From research: one artist hit
**15k monthly listeners / 1.5M streams** doing this by hand — "searching for
playlists I think are good for my music and researching the curator by name... you
have to become a professional stalker." That grind is what we automate. Crucially,
**direct outreach is free and placements are permanent**, whereas SubmitHub /
Groover / Musosoup charge per submission and pull your track after ~a month. The
free, permanent, *uncontested* placements are the whole prize.

---

## 2. What we reuse from Wavy Outreach (unchanged)

The build is fast because most of the plumbing already exists and is proven:

| Component | Reused as-is |
|---|---|
| CSV database + `ensure_schema()` dtype/migration pattern | ✅ |
| GitHub cloud-sync to `app-data` branch (survives Streamlit Cloud reboots) | ✅ |
| `run_apify_and_poll()` Apify actor runner | ✅ |
| `apify/instagram-profile-scraper` IG bio step | ✅ |
| Google-search enrichment step | ✅ |
| `extract_emails()` / `extract_instagram()` / `is_valid_data()` | ✅ |
| Gemini text-cleaning (name normalization) | ✅ |
| Claude.ai free batch-writing loop (download CSV → paste prompt → import CSV) | ✅ |
| Backup/restore ZIP, flash messages, scroll-restore | ✅ |
| `seen_*.json` dedupe memory files | ✅ (new: `seen_playlists.json`, `seen_owners.json`) |
| `st.navigation` / `st.Page` multi-page structure | ✅ |
| WAVYMIXING studio-dark theme (`.streamlit/config.toml`) | ✅ |

**What changes:** the schema (playlist-centric), the Discover page (Spotify search +
qualify instead of YouTube sync), the scoring/filter logic, and the dashboard meters.

---

## 3. Pages

Mirrors Wavy's structure:

| Page | Job |
|---|---|
| **Dashboard** | Funnel meters: Discovered → Qualified → Contactable → Pitched → Replied → **Added**. Plus permanent-placement count, acceptance rate, next-up actions. |
| **Discover** | Keyword search via Apify → filter → score → tier by contact. (Replaces "Collect".) |
| **Write** | Gemini cleans data; Claude.ai batch writes personalized pitches. (Same as Wavy.) |
| **Send** | Paced review queue, one curator at a time, right channel pre-filled, follow-ups. |
| **Playlists** | Full editable table with filters/search. (Replaces "Leads".) |
| **Settings** | API keys, backup/restore, blacklist, **your tracks/EPK links + your monthly-listener range**. |

---

## 4. Data model (playlist / curator schema)

The database row is a **playlist**, but the pipeline **dedupes and pitches by
curator** (one owner = one pitch). Key columns:

**Identity**
`Playlist_ID`, `Playlist Name`, `Playlist URL`, `Owner Name`, `Owner_ID`, `Owner URL`

**Metrics (from scrape)**
`Saves` (= followers), `Track Count`, `Description`, `Median Track Playcount`, `Cover URL`

**Scores & bands (computed locally)**
`Fit Score`, `Quality Score`, `Reachability Bucket`, `Cost Tag`, `In Saves Band`, `In Lane`

**Flags**
`Is Editorial` (owner = "spotify" → skip), `Is Dump Bin` (track count > ~500),
`Is Label Whale`, `Blacklist`, `Remove`

**Contact**
`Email Address`, `Instagram`, `Submission Link`, `Contact Source`
(owner-name / playlist-name / description / submit-playlist / IG-bio / google),
`Contact Confidence`, `IG Bio`, `IG Followers`, `Google Search Status`

**CRM / pipeline** (replaces Wavy's sales states)
`Pitched`, `Pitch_Date`, `Channel` (email/DM/submithub), `Followed Up`, `FollowUp_Date`,
`Replied`, `Added` (to playlist), `Added_Date`, `Declined`, `Notes`

**Draft**
`Draft Pitch`, `🔄 Regenerate`

**Housekeeping**
`Added_Date` (to DB), `🔍 Quick Search`

---

## 5. Discover pipeline (the core)

> **Thesis sharpened:** we're targeting *playlists whose behaviour is to add small artists* —
> not playlists of a certain size. Saves don't reveal that; **who is already on the playlist does.**
> So the **artist-size ("in your lane") band is the primary filter**; the saves band is a
> loose guardrail with a low floor.

### 5.1 Seed
Keyword list only — **reference-artist seeding was cut** (Spotify's real
"Discovered On" data isn't public, so it added cost without unique value).

Keywords = genre + mood + **submission-intent** terms:
`psych rock submit`, `grungegaze submissions`, `minimal electronic curator`.
Submission-intent terms bias the search toward playlists that *want* pitches — the
single biggest free lever on contactable-rate. Batch-capped per keyword; deduped
against `seen_playlists.json`.

### 5.2 Filter out (before spending effort)
- **Editorial** (`owner == "spotify"`) → not curator-pitchable; goes through Spotify for Artists.
- **Dump bins** (track count > ~500) → not curated.
- **Label whales** (huge lists dominated by superstar artists — see Reachability) → closed trade networks.

### 5.3 Four scores
| Score | Question it answers | Computed from |
|---|---|---|
| **Fit** | Does the genre match? | keyword match on name/description/tracks |
| **Quality** | Is this a real, alive curator? | saves + track-count sanity + liveliness proxy |
| **Reachability** | Will an artist *my size* actually get added? | **artist sizes / playcounts of tracks already on it** |
| **Cost Tag** | Free, or will they ask for money? | `free` / `paid-link` (SubmitHub/Groover/$ found) / `unknown` |

### 5.4 Two tunable bands (filter locally, re-slice at zero credit cost)
1. **Saves band** — default **10k–200k** (slider). *Note: the uncontested small
   curators — the goldmine — often sit at 1k–10k, so consider lowering the floor.*
   Job: skip dead lists and untouchable giants. Saves = the only scrapable
   playlist-popularity number.
2. **Artist-size band ("in your lane")** — set relative to *your* monthly listeners
   (from Settings). Artists roughly **1×–15×** your size. **This is the real
   gatekeeper** — it's what stops a 15k-save playlist full of Drake/Yeat/Tyga from
   wasting a pitch.

A playlist must pass **both bands** to enter the primary pitch queue.

### 5.5 Reachability buckets (auto-segregation)
- **Skip / aspirational** — median artist is superstar tier → hidden by default.
- **Stretch** — a few tiers above you → occasional shot.
- **In your lane** — artist sizes cluster near yours → **target bucket**.
- **Below you** — tiny/dead → deprioritized.

Cheap signal = **median track playcount** (free, already scraped — arithmetic, no
extra credits). Accurate signal = per-artist monthly-listener lookup, run only on
keepers (cheapest-first).

> **Listeners/streams per playlist are private and unscrapable** — that number only
> exists inside Spotify for Artists for playlists your own track already sits on. We
> band on **saves** and infer liveliness; we never promise a listener filter.

---

## 6. Contact-discovery waterfall (cheapest-first, stop on first hit)

The contact is smeared across many fields and often on a *different* playlist than
the one you'd pitch. Sweep **all** text fields with the email/@/URL extractors,
normalize-then-extract (flatten stylized unicode + strip emoji **before** regex),
merge and dedupe, and tag the source for confidence.

| Step | Source | Cost | Notes |
|---|---|---|---|
| 1 | **Owner name** | free | Highest-value: often *is* an email/@handle, and is curator-wide. Promote above description-found contacts. |
| 2 | **Playlist name** | free | `Indie Vibes 📩 submit @handle` |
| 3 | **Description** | free | The main mailbox; also where submission-*intent* is detected (Tier A). |
| 4 | **Sibling "Submit Your Music" playlist** | ~1 owner lookup | Curators keep contact on a separate mailbox playlist. Found via owner-pivot (§7). Tag source = `submit-playlist` → inherit cost-tag (often paid). |
| 5 | **IG bio scrape** | Apify per-profile | Only if an @handle turned up and no email yet. Reuses `apify/instagram-profile-scraper`. |

**Google search was cut entirely** — weakest hit rate, highest cost per result. Anything
with no contact after steps 1–5 → **Needs manual look** bucket: you do the targeted
manual stalking on the leads worth it and type the contact in (tagged `manual`,
confidence 100). Step 5 physically cannot fire unless the free steps found an @handle —
this is the core credit protection.

### Manual control is a design principle
Every automated decision is a starting point you own: add a contact by hand, edit any
field, add a playlist without scraping, ❌ delete a row, 🗑️ block a curator (all their
playlists, forever, via `blocked_owners.json`). The automation does the cheap bulk work;
you do the high-value judgement.

### Contact tiers (drives Send ordering)
- **Tier A — Open for submissions:** description/name explicitly invites pitches. Warm.
- **Tier B — Contact present, no explicit invite.** Pitchable.
- **Tier C — Nothing:** goes to enrichment (steps 4–6) or parks.

---

## 7. The owner-pivot (does triple duty)

From any playlist we have the `Owner_ID` / `Owner URL`. Pivoting to the owner once
serves **three** jobs at once, so it's not extra work:

1. **Dedupe the curator** — collapse their 8–10 playlists into one lead / one pitch (anti-spam).
2. **Find the mailbox playlist** — locate their "Submit Your Music"-type sibling and parse its contact.
3. **Read the owner-name contact** — the name itself may be the email/@handle.

If the actor can't list "all playlists by owner" directly, fallback is free: match
siblings by shared `Owner_ID` across results already pulled (curators name mailbox
playlists with genre words, so they usually surface in the same keyword search).

---

## 8. Anti-spam design (this is a feature, not a nicety)

Mass-blasting tanks deliverability and acceptance rates (curators share
blacklists). The app gets volume *without* the spam penalty via:

- **Personalized at volume** — Claude.ai batch writes 30–50 individually tailored
  pitches per pass, each naming the specific playlist and fit.
- **One curator, one pitch** — the owner-dedupe (§7) means a curator running 10
  playlists gets one great message, not ten.
- **Paced sending** — a daily send cap on the Send page meters a batch of 40 out
  over days, protecting your Gmail from throttling.

---

## 9. APIs & cost

| API | Job | ≈ per month |
|---|---|---|
| Apify Spotify actor | discovery | $2–6 / ~400 playlists |
| Apify Instagram scraper | bio → email (gated) | $0.50–1.50 |
| Gemini Flash | language-judgement cleanup only (deterministic unicode/emoji normalize runs free first) | pennies |
| Claude.ai free batch loop | writing pitches | **$0** |
| ~~Google~~ | cut | — |

Runs inside Apify's $5/mo free credit at low volume. SubmitHub/Groover: $1–2 *per submission*,
placements pulled after ~a month. Direct: free, permanent, uncontested. **~50–100× cheaper for a
structurally better outcome.** Real unknown: the per-result price of the chosen actor — confirm on the test run.

## 9b. Credit discipline (summary)

- Free, local, zero-API: all field-sweeping, tiering, scoring, banding, re-slicing.
- Cheap, unavoidable: the search stage (it's what returns descriptions).
- Real cost, hard-gated behind free hits: IG scrape, per-artist size lookups.
- **Submission-keyword search + batch caps + dedupe memory** keep even the cheap
  stage small and skewed toward contactable playlists.
- At ~$0.005/result ballpark, 400 playlists ≈ $2 — so choose the actor on **data
  quality**, not credit micro-optimization.

---

## 10. Actor decision & the one test run

Candidates reviewed:

| Actor | Verdict |
|---|---|
| `automation-lab/spotify-scraper` | ❌ analytics tool; playlist output lacks followers + owner URL |
| Spotify **Playlists** Scraper (search+detail toggle, has `addedAt`) | ✅ backup — only one with freshness signal |
| ScrapeArchitect Playlist Scraper (`playlist_description` in cheap fast mode) | ✅ strong — cheapest contact tier |
| `khadinakbar/spotify-all-in-one-scraper` (flat per-result billing) | ✅ **leading pick** — description + followers + owner in one billed record, plus artist/track lookups for Reachability |

**Leaning `khadinakbar/spotify-all-in-one-scraper`**, conditional on one test run.
Before locking, run **one concise search on a single genre keyword** and confirm:

1. ✅ Each playlist record has `description` **and** `followers` populated (not stripped in search mode).
2. ✅ Actual run cost per result.
3. ✅ Can list a given **owner's other playlists** (for the owner-pivot).
4. ✅ Tracklist **with playcounts** comes back (for Reachability).

If search returns thin records → fall back to ScrapeArchitect's confirmed
cheap-description mode. Grab the exact actor slug off the Apify store page at wiring time.

---

## 11. Out of scope (possible later module)

Running **your own playlist** as an owned asset came up repeatedly in research and
is a legitimate strategy — but it's a different tool. Keep it out of the pitching
app; note as a future module.

---

## 12. Build order

1. Fork Wavy Outreach; swap schema (§4) into `ensure_schema()`; add `seen_playlists.json` / `seen_owners.json`.
2. Run the §10 test; lock the actor; map its real fields to the schema.
3. Build **Discover**: search → filter (§5.2) → sweep contacts (§6) → owner-pivot (§7) → score + band (§5.3–5.4).
4. Wire **Write** (Claude.ai loop, unchanged) and **Send** (add paced cap + curator-dedupe queue).
5. Dashboard meters + Playlists table with the new filters (buckets, tiers, cost tags).
6. Keep cloud-sync, backup, theme from Wavy as-is.

---

*Status: **built** (see `app.py`). Genre/keywords, monthly listeners and channels are runtime
Settings. Remaining: run the §10 test scrape and confirm the actor's fields via Discover → Raw sample.*
