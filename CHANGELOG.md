# Changelog

## v1.4 — current (validated against a real 139-playlist run)

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
