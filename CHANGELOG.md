# Changelog

## v2.0 — current: ONE hardwired actor that does everything

- **Hardwired to the “Spotify Playlists” actor** (`terms / startUrls / maxItems / maxTracks / expand / proxyConfiguration`). Its output (`playlistId, playlistName, description, ownerName, ownerId, followers, totalTracks, tracks[].plays, tracks[].addedAt`) is the only one found with keyword search + followers + playcounts + **added-dates** in one pass.
- Removed the template / schema-reading UI and the details-actor slot — no field-name guessing anywhere. One config field: the actor slug or ID.
- **Real Freshness** (Fresh ≤30d / Active ≤90d / Stale ≤1y / Dormant) from `addedAt`; **Reachability** from real per-track plays; mapper also reads Spotify popularity when an actor provides it instead.
- URL mode sends Apify-standard `startUrls: [{"url"}]` and automatically retries with plain strings if the actor rejects it.
- Tracks per playlist default 200 (freshness needs the end of the playlist); Apify proxy off by default (actor uses Spotify's API); spend cap off by default.
- End-to-end verified against the actor's documented sample: editorial / instrumental / superstar rows skipped, contact + freshness + reachability filled.

## v1.6: one full-detail pass, no guessed field names

- **Default is now ONE PASS with full details on**: every result arrives with saves, track count, playcounts and (if the actor provides it) per-track added-dates — so Freshness, Reachability and Quality are filled on the first run. Costs more per result; the hard cap (default now $2) is the ceiling.
- **Settings → "Read input schema from Apify"**: the app pulls the actor's real input fields from Apify and proposes the search/URL templates automatically (keywords / limit / details / URLs / track limit). Review, apply, done — no hand-typed field names, works with any playlist actor. Recommended actor: the *Spotify Playlists Scraper* (returns `addedAt`).
- Cheap description-only mode still available as a toggle; step 3 becomes an optional "refresh details" for rows scraped before full-detail mode or added by hand.

## v1.5

- **Mainstream / chart / famous-artist playlists skipped at ingest** ("famous songs", "Top 100", "chart hits", "X Radio", or 2+ megastar name-drops; 3 if the text also signals indie-friendliness). Genre-taste names (Nujabes, Loyle Carner…) and vague words ("classics", "legends") deliberately excluded. 19/139 flagged on real data; "5pm hooping — unknown talents on the rise" correctly kept. Also sets Reachability = *Skip (superstars)* from text alone, so it works with no playcounts.
- **Freshness** column: from `Last Added` when the actor returns per-track dates (Fresh ≤30d / Active ≤90d / Stale ≤1y / Dormant); otherwise the curator's own claim ("Claims: updated weekly") as a labelled hint, never as fact.
- **Optional details actor**: a second actor slot for the details step (which only runs on contactable playlists). Point it at an actor that returns `addedAt` — e.g. the *Spotify Playlists Scraper* — to populate freshness. `custom` style accepts any actor's input JSON with `__URLS__`, so no code change is needed for a new actor.
- Details fetch never overwrites known saves/track counts with zeros from a thin record.
- **Main actor `custom` style**: paste any playlist actor's input JSON with `__KEYWORDS__` / `__LIMIT__` — lets you run the *Spotify Playlists Scraper* (returns `addedAt`) as your ONLY actor, for contacts + saves + freshness in one, if its cheap search mode returns descriptions.

## v1.4 (validated against a real 139-playlist run)

**Cost control**
- Every Apify run starts with a **hard spend cap** (`max_total_charge_usd`, default $1.00, Settings) and an item cap. Apify aborts the run at the cap.
- **Fast mode by default**: search returns description + owner (the contact fields) only. Saves/tracklists are fetched later via *Discover → step 3*, **only for playlists that already have a contact**.
- Default batch 20 results/keyword (was 40). Skipped playlists go into seen-memory and are never re-scraped.
- Default actor style switched to a **dedicated playlist scraper**; the all-in-one actor is warned against for search (bills ~6 entity types per keyword). App refuses to run until an actor slug is set.

**Contact discovery (15 → 27 contacts found on the same real data)**
- HTML in descriptions is unescaped (`&amp;`, `&#x2F;`) and URLs are lifted out of `<a href>` tags.
- Submission platforms added: `sbmt.to` (SubmitHub short link), `upnextapp.io`, `dailyplaylists`, `indiemono`, `submit.link`; bare domains without `https://` are recognised.
- Bare `@handles` in descriptions/names are accepted as Instagram (lower confidence). Never taken from inside an email.
- Curator **websites** in descriptions are captured as contacts (Spotify/YouTube/Apple/smart links excluded; an email's domain is never mistaken for a site).
- Owner names that are websites (`Top50Clean.com`) or `@handles` count as contacts.
- Submission-intent detection in **Spanish, Portuguese, French, German, Italian**.
- **Negation-aware**: "NO SUBMISSIONS", "not accepting", "don't send", "submissions closed" → curator auto-marked *Declined* with a note, never enters the queue.
- Link-only contacts now carry a source and confidence.

**Filtering & triage**
- **Mainstream / superstar playlists skipped at ingest.** Hard words (famous, popular songs, chart, Top 100, hits, billboard, viral, trending, best of, greatest) always skip; two-plus mega-stars (Drake, Eminem, Kanye, Kendrick, J Cole, Travis Scott…) skip, three if the text also signals indie/underground. Genre-taste names (Nujabes, Dilla, Little Simz, Noname, Loyle Carner) and vague words (classics, legends, essentials) deliberately do NOT trigger it. Validated: 18/139 skipped on real data, all lane playlists kept. Toggle in Settings.
- **Type-beat / instrumental playlists skipped at ingest.** Name-decisive ("Boom Bap Beats", "Hip Hop Instrumentals", "J Cole Type Beats", "no lyrics"); descriptions only count if they say instrumental *and* never mention rap/vocals/bars/flows. "Nothing beats jazz rap" (verb) and "Rap & Beats" (mixed) are kept. Toggle + extra-words box in Settings.
- **No-contact playlists hidden by default** in Playlists (checkbox to show). Kept stored so a sibling "Submit Your Music" playlist can fill them later; optional Settings toggle to discard outright.
- New **`Invites Subs`** flag: playlists that invite pitches but hide the contact are listed **first** under *Needs manual look* and called out on the Dashboard — the best manual-stalking targets.
- New **`Last Added`** column (recency) populated when the actor returns per-track `addedAt`.
- Discover summary reports the full ingest breakdown (kept / with contact / no contact / instrumental / editorial / seen / blocked).

**Bug fixes**
- Playlists **"All" filter** showed only Tier A (`"All".startswith("A")`). Fixed.
- Applying table edits no longer tags untouched blank cells as `manual`.
- `use_container_width` → `width="stretch"` (deprecated API removed); Streamlit pinned `>=1.63,<2`.
- IG regex matched `ig` inside words ("N**ig**ht") and invented handles. Fixed with word boundaries.

## v1.0 — initial fork of Wavy Outreach
Playlist-centric schema; Discover / Write / Send / Playlists / Settings; GitHub auto-save to `app-data` branch after every interaction; Claude.ai free batch pitch loop; one pitch per curator; daily send cap; follow-ups; manual add/edit/delete/block.
