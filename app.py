"""
Wavy Pitch — pitch your music to curated Spotify playlists.

Discover user-curated playlists in your genre via Apify, find the curator's
contact (owner name → playlist name → description → sibling "submit" playlist →
Instagram bio), score every playlist for fit / quality / reachability, write
personalised pitches through Claude.ai (free), send + follow up, and track
placements through to permanent adds. Forked from Wavy Outreach — same database
pattern, same GitHub auto-save, same Apify + Gemini + Claude.ai plumbing.
"""

import io
import os
import re
import json
import time
import base64
import hashlib
import zipfile
import unicodedata
import html as html_lib
import urllib.request
import urllib.error
from statistics import median
from urllib.parse import quote

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from google import genai as google_genai
from google.genai import types as genai_types

# ----------------------------------------------------------------------------
# App config
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Wavy Pitch",
    page_icon="🎧",
    layout="wide",
    initial_sidebar_state="expanded",
)

ACCENT = "#F0A93B"  # keep in sync with .streamlit/config.toml primaryColor

DB_FILE = "wavy_pitch_db.csv"
SEEN_PLAYLISTS_FILE = "seen_playlists.json"
BLOCKED_OWNERS_FILE = "blocked_owners.json"
SCRAPED_IGS_FILE = "scraped_igs.json"
PROFILE_FILE = "my_profile.json"

APIFY_POLL_TIMEOUT_SECS = 15 * 60
GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ACTOR = "khadinakbar/spotify-all-in-one-scraper"
FOLLOW_UP_DAYS = 7

KEY_NAMES = {"APIFY_API_TOKEN": "Apify", "GEMINI_API_KEY": "Gemini"}

def get_secret(name):
    try:
        val = st.secrets.get(name)
        if val:
            return val
    except Exception:
        pass
    return os.getenv(name, "")

def get_key(name):
    override = str(st.session_state.get(f"key_{name}", "") or "").strip()
    return override or get_secret(name)

# ----------------------------------------------------------------------------
# Your profile — the artist being pitched + tunable filters. Persisted + synced.
# ----------------------------------------------------------------------------
DEFAULT_PROFILE = {
    "artist_name": "",
    "genre": "",
    "monthly_listeners": 1000,
    "track_link": "",
    "epk_link": "",
    "one_liner": "",
    "keywords": "",
    "saves_min": 1000,
    "saves_max": 200000,
    "lane_min_plays": 50000,
    "lane_max_plays": 5000000,
    "dump_bin_tracks": 500,
    "daily_cap": 15,
    "actor_id": "",                 # paste the exact slug from the Apify store page
    "actor_style": "playlist-scraper",  # dedicated playlist actor: returns ONLY playlists (no wasted entity types)
    "results_per_keyword": 20,
    "fetch_details": False,          # fast mode = description/owner (contacts) cheaply; details (saves/tracks) on demand
    "skip_instrumental": True,       # drop type-beat / instrumental playlists at ingest (they won't add vocals)
    "skip_mainstream": True,         # drop "famous songs / Top 100 / chart hits / Drake, Eminem, Kanye…" playlists at ingest
    "exclude_words": "type beat, instrumental, beat tape, producer pack",  # extra NAME words to skip
    "keep_no_contact": True,         # store Tier-C rows (hidden by default) so sibling contacts can still fill them
    "max_usd_per_run": 1.0,          # hard cap — Apify aborts the run at this spend
}

def load_profile():
    prof = dict(DEFAULT_PROFILE)
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, "r") as f:
                prof.update(json.load(f) or {})
        except Exception:
            pass
    return prof

def save_profile(prof):
    with open(PROFILE_FILE, "w") as f:
        json.dump(prof, f, indent=2)

# ----------------------------------------------------------------------------
# Schema — one row per playlist; pitched + deduped per curator (Owner_ID)
# ----------------------------------------------------------------------------
COLUMN_ORDER = [
    "🗑️ Block", "❌ Remove",
    "Pitched", "Followed Up", "Replied", "Added", "Declined",
    "Pitch_Date", "FollowUp_Date", "Added_Date", "Added_To_DB",
    "Playlist_ID", "Playlist Name", "Playlist URL", "Owner Name", "Owner_ID", "Owner URL",
    "Saves", "Track Count", "Median Playcount", "Last Added", "Description", "Keyword",
    "Contact Tier", "Reachability", "Cost Tag", "Fit Score", "Quality Score",
    "Email Address", "Instagram", "Submission Link", "Contact Source", "Contact Confidence",
    "IG Bio", "IG Followers",
    "Channel", "Draft Pitch", "🔄 Regenerate", "Notes",
    "Is Editorial", "Is Dump Bin", "Invites Subs",
]

TEXT_DEFAULTS = {
    "Playlist_ID": "", "Playlist Name": "", "Playlist URL": "", "Owner Name": "", "Owner_ID": "",
    "Owner URL": "", "Description": "", "Keyword": "",
    "Contact Tier": "C", "Reachability": "Unknown", "Cost Tag": "unknown",
    "Email Address": "None Found", "Instagram": "None", "Submission Link": "",
    "Contact Source": "", "IG Bio": "Not Scanned",
    "Channel": "", "Draft Pitch": "", "Notes": "",
}
BOOL_COLS = ["🗑️ Block", "❌ Remove", "Pitched", "Followed Up", "Replied", "Added", "Declined",
             "🔄 Regenerate", "Is Editorial", "Is Dump Bin", "Invites Subs"]
INT_COLS = ["Saves", "Track Count", "Median Playcount", "Fit Score", "Quality Score",
            "Contact Confidence", "IG Followers"]
DATE_COLS = ["Pitch_Date", "FollowUp_Date", "Added_Date", "Added_To_DB", "Last Added"]
UI_ONLY_COLS = ["🗑️ Block", "❌ Remove"]

def ensure_schema(df):
    """Add missing columns, fix dtypes, order columns. Never raises."""
    if df is None:
        return pd.DataFrame(columns=COLUMN_ORDER)
    df = df.copy()
    for col, default in TEXT_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna(default).astype(str).replace("nan", default)
    for col in BOOL_COLS:
        if col not in df.columns:
            df[col] = False
        df[col] = df[col].map(lambda v: str(v).strip().lower() == "true" if not isinstance(v, bool) else v)
        df[col] = df[col].fillna(False).astype(bool)
    for col in INT_COLS:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    for col in DATE_COLS:
        if col not in df.columns:
            df[col] = pd.NaT
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df["Draft Pitch"] = df["Draft Pitch"].apply(lambda v: "" if str(v).strip().startswith("[Claude Error") else v)
    df["Instagram"] = df["Instagram"].apply(lambda x: str(x).split(",")[0].strip() if is_valid_data(x) else "None")
    ordered = [c for c in COLUMN_ORDER if c in df.columns]
    extras = [c for c in df.columns if c not in ordered]
    return df[ordered + extras]

def load_db():
    if os.path.exists(DB_FILE):
        try:
            return ensure_schema(pd.read_csv(DB_FILE))
        except Exception as e:
            st.warning(f"Could not read {DB_FILE} ({e}). Starting with an empty database.")
    return ensure_schema(pd.DataFrame())

def save_db(df):
    df.drop(columns=UI_ONLY_COLS, errors="ignore").to_csv(DB_FILE, index=False)

def load_json_set(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                return set(str(item) for item in json.load(f))
        except Exception:
            return set()
    return set()

def save_json_set(data_set, filepath):
    with open(filepath, "w") as f:
        json.dump(sorted(str(item) for item in data_set), f)

# ----------------------------------------------------------------------------
# Cloud save — every data file lives on a GitHub branch so it survives
# Streamlit Cloud reboots. Runs at the end of EVERY script run (i.e. after every
# click/edit) and pushes only files whose contents changed. Ported verbatim
# from Wavy Outreach.
#
# One-time setup:
#   1. GitHub → Settings → Developer settings → Fine-grained tokens → new token
#      scoped to ONLY this repo, permission Contents: Read & write.
#   2. Streamlit Cloud → App → Settings → Secrets → add
#         GITHUB_TOKEN = "github_pat_..."
#         GITHUB_REPO  = "yourname/wavypitch"
# Data goes to the `app-data` branch — separate from code, so it never
# triggers a redeploy (Streamlit watches main).
# ----------------------------------------------------------------------------
PERSIST_FILES = [DB_FILE, SEEN_PLAYLISTS_FILE, BLOCKED_OWNERS_FILE, SCRAPED_IGS_FILE, PROFILE_FILE]
CLOUD_STATE_FILE = ".cloud_state.json"
CLOUD_RESTORED_MARKER = ".cloud_restored"
CLOUD_BRANCH_MARKER = ".cloud_branch_ok"

def _cloud_token():
    return str(get_secret("GITHUB_TOKEN") or "").strip()

def _cloud_repo():
    return str(get_secret("GITHUB_REPO") or "alexsamey95/wavypitch").strip()

def _cloud_branch():
    return str(get_secret("GITHUB_DATA_BRANCH") or "app-data").strip()

def cloud_enabled():
    return bool(_cloud_token())

def _gh_headers(accept="application/vnd.github+json"):
    return {
        "Authorization": f"Bearer {_cloud_token()}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "wavy-pitch",
    }

def _gh(method, path, payload=None):
    req = urllib.request.Request(
        f"https://api.github.com/{path}",
        method=method,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=_gh_headers(),
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None

def _gh_fetch_raw(path):
    url = (f"https://api.github.com/repos/{_cloud_repo()}/contents/{quote(path)}"
           f"?ref={quote(_cloud_branch())}")
    req = urllib.request.Request(url, headers=_gh_headers("application/vnd.github.raw+json"))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise

def _git_blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

def _ensure_data_branch():
    repo, branch = _cloud_repo(), _cloud_branch()
    try:
        _gh("GET", f"repos/{repo}/branches/{quote(branch)}")
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    base = (_gh("GET", f"repos/{repo}") or {}).get("default_branch", "main")
    ref = _gh("GET", f"repos/{repo}/git/ref/heads/{quote(base)}")
    _gh("POST", f"repos/{repo}/git/refs",
        {"ref": f"refs/heads/{branch}", "sha": ref["object"]["sha"]})

def _gh_put_file(path, data, sha):
    payload = {"message": f"wavy pitch data: update {path}",
               "content": base64.b64encode(data).decode("ascii"),
               "branch": _cloud_branch()}
    if sha:
        payload["sha"] = sha
    try:
        _gh("PUT", f"repos/{_cloud_repo()}/contents/{quote(path)}", payload)
    except urllib.error.HTTPError as e:
        if e.code not in (409, 422):
            raise
        remote = _gh_fetch_raw(path)
        if remote is None:
            payload.pop("sha", None)
        else:
            payload["sha"] = _git_blob_sha(remote)
        _gh("PUT", f"repos/{_cloud_repo()}/contents/{quote(path)}", payload)

def _gh_delete_file(path, sha):
    payload = {"message": f"wavy pitch data: delete {path}", "sha": sha or "", "branch": _cloud_branch()}
    try:
        _gh("DELETE", f"repos/{_cloud_repo()}/contents/{quote(path)}", payload)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return
        if e.code not in (409, 422):
            raise
        remote = _gh_fetch_raw(path)
        if remote is None:
            return
        payload["sha"] = _git_blob_sha(remote)
        _gh("DELETE", f"repos/{_cloud_repo()}/contents/{quote(path)}", payload)

def _cloud_state_load():
    if os.path.exists(CLOUD_STATE_FILE):
        try:
            with open(CLOUD_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _cloud_state_save(state):
    with open(CLOUD_STATE_FILE, "w") as f:
        json.dump(state, f)

def _cloud_err_msg(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 401:
            return "GitHub token is invalid or expired — update GITHUB_TOKEN in the app secrets."
        if e.code == 403:
            return "GitHub token lacks permission — it needs Contents: Read and write on the repo."
        if e.code == 404:
            return (f"GitHub repo `{_cloud_repo()}` not found or the token can't see it — "
                    "check GITHUB_REPO / the token's repository access.")
        return f"GitHub error {e.code}"
    return str(e) or type(e).__name__

def cloud_pending_count():
    state = _cloud_state_load()
    n = 0
    for path in PERSIST_FILES:
        if os.path.exists(path):
            with open(path, "rb") as f:
                if state.get(path, {}).get("md5") != hashlib.md5(f.read()).hexdigest():
                    n += 1
        elif path in state:
            n += 1
    return n

def _cloud_sync_core():
    if not cloud_enabled():
        return 0
    if not os.path.exists(CLOUD_BRANCH_MARKER):
        _ensure_data_branch()
        with open(CLOUD_BRANCH_MARKER, "w") as f:
            f.write("ok")
    state = _cloud_state_load()
    pushed = 0
    for path in PERSIST_FILES:
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            digest = hashlib.md5(data).hexdigest()
            if state.get(path, {}).get("md5") == digest:
                continue
            _gh_put_file(path, data, state.get(path, {}).get("sha"))
            state[path] = {"md5": digest, "sha": _git_blob_sha(data)}
            pushed += 1
        elif path in state:
            _gh_delete_file(path, state[path].get("sha"))
            del state[path]
            pushed += 1
    if pushed:
        _cloud_state_save(state)
    return pushed

def cloud_sync():
    if not cloud_enabled():
        return 0, None
    try:
        pushed = _cloud_sync_core()
        if pushed:
            st.session_state["_cloud_last_sync"] = time.time()
        st.session_state["_cloud_error"] = None
        return pushed, None
    except Exception as e:
        msg = _cloud_err_msg(e)
        st.session_state["_cloud_error"] = msg
        return 0, msg

def cloud_restore_on_boot():
    if not cloud_enabled() or os.path.exists(CLOUD_RESTORED_MARKER):
        return
    try:
        _ensure_data_branch()
        with open(CLOUD_BRANCH_MARKER, "w") as f:
            f.write("ok")
        state = _cloud_state_load()
        restored = 0
        for path in PERSIST_FILES:
            if os.path.exists(path):
                continue
            data = _gh_fetch_raw(path)
            if data is None:
                continue
            with open(path, "wb") as f:
                f.write(data)
            state[path] = {"md5": hashlib.md5(data).hexdigest(), "sha": _git_blob_sha(data)}
            restored += 1
        _cloud_state_save(state)
        if restored:
            st.toast(f"☁️ Restored {restored} data file(s) from GitHub.")
    except Exception as e:
        st.warning(f"Could not restore data from GitHub ({_cloud_err_msg(e)}). Starting with what's on disk.")
    finally:
        with open(CLOUD_RESTORED_MARKER, "w") as f:
            f.write(str(time.time()))

def persist(df=None):
    """Save the DB (if given) and push everything to GitHub right away.
    Called after every mutation so cloud save happens at every step."""
    if df is not None:
        save_db(df)
    cloud_sync()

# ----------------------------------------------------------------------------
# Parsing helpers — normalize FIRST (curators stylize names with fancy unicode
# and emoji), THEN extract. Otherwise a stylized email silently fails to match.
# ----------------------------------------------------------------------------
def is_valid_data(val):
    v = str(val).lower().strip()
    return v not in ["none", "none found", "", "nan", "not scanned", "empty bio", "unknown"]

def safe_int(val, default=0):
    try:
        v = pd.to_numeric(val, errors="coerce")
        return default if pd.isna(v) else int(v)
    except Exception:
        return default

def normalize_text(text):
    """Unescape HTML (&amp; &#x2F; …), lift URLs out of <a href> tags, strip tags,
    flatten stylized unicode (𝐛𝐨𝐥𝐝, 𝕗𝕒𝕟𝕔𝕪) and strip emoji. Spotify descriptions
    arrive HTML-escaped, so this must run before any regex."""
    if not text:
        return ""
    t = html_lib.unescape(html_lib.unescape(str(text)))  # double-escaped entities are common
    t = re.sub(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>', r' \1 ', t, flags=re.IGNORECASE)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = unicodedata.normalize("NFKC", t)
    t = "".join(ch for ch in t if not (0x1F000 <= ord(ch) <= 0x1FAFF or 0x2600 <= ord(ch) <= 0x27BF
                                      or 0xFE00 <= ord(ch) <= 0xFE0F or ord(ch) == 0x200D))
    return re.sub(r"\s+", " ", t).strip()

FAKE_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp3", ".mp4", ".wav")

def extract_emails(text):
    if not text:
        return []
    text = normalize_text(text)
    text = re.sub(
        r'\b([a-zA-Z0-9._%+-]+)\s*(?:@|\(at\)|\[at\]|\bat\b)\s*([a-zA-Z0-9.-]+)\s*(?:\.|\(dot\)|\[dot\]|\bdot\b)\s*([a-zA-Z]{2,})\b',
        r'\1@\2.\3', text, flags=re.IGNORECASE,
    )
    found = re.findall(r'[a-zA-Z0-9%._+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    return [e for e in set(found) if not e.lower().endswith(FAKE_EMAIL_SUFFIXES)]

def extract_instagram(text):
    """Return an instagram.com URL or 'None'. Catches links, 'ig: @x', and bare @handles
    when the surrounding text mentions IG/insta/DM."""
    if not text:
        return "None"
    text = normalize_text(text)
    all_urls = re.findall(r'(https?://[^\s<>"]+|www\.[^\s<>"]+)', text)
    links = []
    for url in all_urls:
        ul = url.lower()
        if 'instagram.com' in ul and not any(b in ul for b in ['/p/', '/reel/', '/reels/', '/explore/', '/tags/', '/audio/']):
            clean = url.split('?')[0].rstrip('/')
            if clean.lower().startswith("www."):
                clean = "https://" + clean
            links.append(clean)
    for h in re.findall(r'\b(?:ig|insta|instagram)\b\s*[:\-–]?\s*@?([a-zA-Z0-9_.]{2,30})', text, re.IGNORECASE):
        if h.lower() not in ("https", "http", "com"):
            links.append(f"https://instagram.com/{h}")
    if not links:
        # A lone @handle in a Spotify description is nearly always a social handle.
        for h in re.findall(r'(?<![\w.])@([a-zA-Z0-9_.]{3,30})(?![\w.]*\.[a-z]{2,}\b(?![./]))', text):
            h = h.rstrip(".")
            if h.lower() not in ("gmail", "hotmail", "outlook", "yahoo") and not re.search(r'\.(com|net|org|io)$', h, re.I):
                links.append(f"https://instagram.com/{h}")
                break
    return links[0] if links else "None"

def extract_ig_username(ig_url):
    m = re.search(r'instagram\.com/([a-zA-Z0-9_.-]+)', str(ig_url), re.IGNORECASE)
    if m:
        return m.group(1).split("?")[0].strip("/")
    return str(ig_url).replace("@", "").strip()

SUBMISSION_PLATFORMS = r'(?:sbmt\.to|submithub|groover|musosoup|playlistpush|upnextapp|dailyplaylists|soundcampaign|indiemono|submit\.link|linktr\.ee|forms\.gle|docs\.google\.com/forms|typeform|beacons\.ai|bio\.link)'
SUBMISSION_LINK_RE = re.compile(r'((?:https?://)?(?:www\.)?[^\s<>"\']*' + SUBMISSION_PLATFORMS + r'[^\s<>"\']*)', re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(r'^(?:https?://)?(?:www\.)?([a-z0-9-]+\.(?:com|net|io|co|app|fm|to|link|me|org|music))(?:/\S*)?$', re.IGNORECASE)
PAID_RE = re.compile(r'(sbmt\.to|submithub|groover|musosoup|playlist\s*push|upnextapp|dailyplaylists|soundcampaign|\$\s?\d|\d\s?(usd|eur|€|£)|paid\s*(promo|placement|submission)|\bfee\b|pay\s*to\s*(play|submit))', re.IGNORECASE)
INTENT_RE = re.compile(
    r'\b(submit|submission|submissions|pitch|for\s+consideration|send\s+(us|me)\s+your|dm\s+(me|us)?\s*(for|to)|email\s+(me|us)?\s*(for|to|at)|add\s+your|want\s+to\s+be\s+(added|featured)|open\s+for|accepting'
    r'|tienes\s+temas|env[ií]a|manda|m[aá]ndanos|contacto|cont[aá]ctame|por\s+aqu[ií]'          # es
    r'|envie|mande|submeta|contato'                                                             # pt
    r'|envoyez|soumettre|proposer|contactez'                                                     # fr
    r'|einreichen|schick|kontakt'                                                                # de
    r'|invia|inviate|proponi|contatta)\b', re.IGNORECASE)
SUBMIT_PLAYLIST_NAME_RE = re.compile(r'\b(?:submit|submission|submissions|contact|read\s+description|how\s+to\s+(?:submit|get\s+added)|send\s+your)\b', re.IGNORECASE)

NO_SUBS_RE = re.compile(
    r"\b(no\s+(?:more\s+)?(?:submissions?|subs|pitches|requests|promo|promotion|dms?)|not\s+(?:accepting|taking|open\s+(?:to|for))\s+(?:any\s+)?(?:submissions?|subs|pitches|requests)"
    r"|do\s*n[o']t\s+(?:send|submit|pitch|dm)|closed\s+(?:to|for)\s+submissions?|submissions?\s+(?:are\s+)?closed|don'?t\s+ask\s+(?:me\s+)?to\s+add"
    r"|no\s+se\s+aceptan|non\s+accetto|keine\s+(?:einreichungen|anfragen)|pas\s+de\s+soumissions?)\b", re.IGNORECASE)

def refuses_submissions(*texts):
    return any(NO_SUBS_RE.search(normalize_text(t) or "") for t in texts if t)

# A curator's own website in the description is a door too (it'll have a contact page).
# Skip platforms that aren't contacts: Spotify itself, YouTube, Apple, image/CDN links.
NON_CONTACT_DOMAINS = re.compile(r"(spotify\.com|spoti\.fi|youtube\.com|youtu\.be|music\.apple\.com|apple\.com|scdn\.co|i\.scdn|freepik|unsplash|pexels|giphy|imgur|wikipedia\.org|google\.com/search"
                                 r"|lnk\.to|linkfire|ffm\.to|fanlink|song\.link|hypeddit|distrokid|ditto|deezer|tidal|amazon\.|soundcloud\.com/[^/]+/sets)", re.I)
WEBSITE_RE = re.compile(r"((?:https?://|www\.)[^\s<>\"'()\[\]]+|\b(?!e\.g\b)[a-z0-9-]{2,}(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|co|me|fm|link|app|music|xyz|site|page|store|shop|band|rocks)\b(?:/[^\s<>\"'()\[\]]*)?)", re.I)

def extract_website(text):
    """First non-platform website URL in the text, or ''. Runs after the submission-platform check."""
    if not text:
        return ""
    t = re.sub(r'[a-zA-Z0-9%._+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', ' ', normalize_text(text))  # never mistake an email's domain for a website
    for m in WEBSITE_RE.finditer(t):
        u = m.group(1).rstrip(".,;:!?)|]'\"")
        if NON_CONTACT_DOMAINS.search(u) or "@" in u or re.search(r"\.(png|jpe?g|gif|webp|svg|mp3|mp4|wav)$", u, re.I):
            continue
        if len(u.split(".")[0].replace("https://", "").replace("http://", "").replace("www.", "")) < 2:
            continue
        return u if u.lower().startswith("http") else "https://" + u
    return ""

def extract_submission_link(text):
    if not text:
        return ""
    m = SUBMISSION_LINK_RE.search(normalize_text(text))
    if not m:
        return ""
    u = m.group(1).split()[0].rstrip('.,)|]')
    return u if u.lower().startswith("http") else "https://" + u

def has_submission_intent(*texts):
    """True if the text invites pitches — and is NOT a negation like 'NO SUBMISSIONS'."""
    if refuses_submissions(*texts):
        return False
    return any(INTENT_RE.search(normalize_text(t) or "") for t in texts if t)

def cost_tag_for(*texts):
    blob = " ".join(normalize_text(t) for t in texts if t)
    return "paid-link" if PAID_RE.search(blob) else ""

def sweep_contacts(owner_name, playlist_name, description):
    """Sweep every text field for contacts. Returns dict with email, instagram,
    submission link, source (where the best contact came from) and confidence.
    Owner-name contacts rank highest: they're curator-wide."""
    fields = [("owner-name", owner_name, 90), ("playlist-name", playlist_name, 70), ("description", description, 60)]
    email, ig, link, source, conf = "", "", "", "", 0
    for src, txt, weight in fields:
        if not txt:
            continue
        if not email:
            found = extract_emails(txt)
            if found:
                email, source, conf = sorted(found)[0], src, max(conf, weight)
        if not ig:
            got = extract_instagram(txt)
            if got != "None":
                ig = got
                if not source:
                    source, conf = src, weight - 10
        if not link:
            link = extract_submission_link(txt)
            if link:
                source, conf = source or src, max(conf, 45)
            elif src != "owner-name":
                link = extract_website(txt)
                if link:
                    source, conf = source or src, max(conf, 40)
    # The owner display name itself may literally be an @handle or a website
    if owner_name:
        on = normalize_text(owner_name)
        if not ig and re.fullmatch(r'@?[a-zA-Z0-9_.]{3,30}', on) and on.startswith("@"):
            ig, source, conf = f"https://instagram.com/{on[1:]}", "owner-name", max(conf, 80)
        dm = BARE_DOMAIN_RE.match(on)
        if not link and dm and not email:
            link = "https://" + dm.group(1).lower()
            source, conf = source or "owner-name", max(conf, 55)
    if has_submission_intent(playlist_name, description) and (email or ig or link):
        conf = min(100, conf + 10)
    return {"email": email, "instagram": ig, "link": link, "source": source, "confidence": conf}

# Instrumental / type-beat detection — a playlist that won't add a vocal track.
# The NAME is decisive ("Boom Bap Beats", "Hip Hop Instrumentals", "J Cole Type Beats").
# The DESCRIPTION only counts if it says instrumental AND never mentions rap/vocals/bars,
# because mixed playlists ("instrumental and vocal selections") do add vocals.
INSTR_NAME_RE = re.compile(r"\b(type\s*beats?|instrumentals?|beats?|no\s+(?:lyrics|vocals)|beat\s*tape|study\s+beats?)\b", re.I)
INSTR_DESC_RE = re.compile(r"\b(type\s*beats?|instrumentals?|no\s+(?:lyrics|vocals)|instrumentals?\s+only|producers?\s+includes?|beat\s*tape)\b", re.I)
VOCAL_RE = re.compile(r"\b(vocals?|rap|rappers?|bars|flows?|singing|singers?|lyricis[mt]|lyrics|mcs?|verses?|songs?)\b", re.I)

def looks_instrumental(name, description, extra_words=""):
    name_n, desc_n = normalize_text(name), normalize_text(description)
    extras = [w.strip() for w in re.split(r"[,\n]", extra_words or "") if w.strip()]
    if extras and re.search(r"\b(" + "|".join(re.escape(w) for w in extras) + r")\b", name_n, re.I):
        return True
    name_v = re.sub(r"\bnothing\s+beats\b", "", name_n, flags=re.I)  # "Nothing beats jazz rap" — verb, not instrumentals
    mixed = re.search(r"\b(?:beats?\s*(?:and|&|\+|,|/)\s*(?:rap|vocals|bars|rhymes|flows|songs)|(?:rap|vocals|bars|rhymes|flows|songs)\s*(?:and|&|\+|,|/)\s*beats?)\b", name_v, re.I)
    if INSTR_NAME_RE.search(name_v) and not mixed:
        return True
    if INSTR_DESC_RE.search(desc_n) and not VOCAL_RE.search(name_n + " " + desc_n.replace("no lyrics", "").replace("no vocals", "")):
        return True
    return False

# Mainstream / superstar playlists — "famous songs", "Top 100", "chart hits", or a roll-call of
# mega-stars. They don't add unknowns. Readable for free from the text, before any tracklist fetch.
# Deliberately NOT included: genre-taste names (Nujabes, Dilla, Little Simz, Noname, Loyle Carner…)
# and vague words (classics, legends, essentials) — jazz-rap curators use those constantly.
MAINSTREAM_HARD_RE = re.compile(
    r"\b(famous|popular\s+(?:songs?|rap|hip\s*hop|music|tracks?|artists?)|most\s+popular|chart(?:s|ing|\s*toppers?)?|billboard|top\s*\d{2,3}|hits?|greatest(?:\s+hits)?|best\s+of|viral|trending|tiktok\s+(?:songs?|rap|hits?)|hall\s+of\s+fame)\b", re.I)
MEGASTAR_RE = re.compile(
    r"\b(drake|eminem|kanye(?:\s+west)?|kendrick(?:\s+lamar)?|j\.?\s*cole|travis\s+scott|tyler[,]?\s+the\s+creator|childish\s+gambino|post\s+malone|lil\s+(?:baby|wayne|uzi|durk|yachty|tjay|nas\s+x)|future|jay[\s-]?z|tupac|2pac|biggie|snoop(?:\s+dogg)?|dr\.?\s*dre|50\s*cent|kid\s+cudi|mac\s+miller|logic|a\$ap\s+rocky|asap\s+rocky|frank\s+ocean|brent\s+faiyaz|sza|the\s+weeknd|don\s+toliver|21\s+savage|metro\s+boomin|gunna|young\s+thug|playboi\s+carti|juice\s+wrld|xxxtentacion|nba\s+youngboy|rod\s+wave|polo\s+g|jack\s+harlow|megan\s+thee\s+stallion|cardi\s+b|nicki\s+minaj|doja\s+cat|ice\s+spice|central\s+cee|stormzy|dave|big\s+sean|chance\s+the\s+rapper|nelly|ludacris|t\.?i\.?|rick\s+ross|nas|jid|quavo|migos|offset|lil\s+durk|yeat|ken\s+carson|destroy\s+lonely|drake)\b", re.I)
INDIE_SIGNAL_RE = re.compile(
    r"\b(undiscovered|unknown|underground|up[\s-]and[\s-]coming|upcoming|emerging|indie|independent|new\s+artists?|unsigned|rising|hidden\s+gems?|talents?\s+on\s+the\s+rise|small\s+artists?|submit|submissions?|discover(?:y|ies)?)\b", re.I)

def looks_mainstream(name, description):
    t = normalize_text(f"{name} {description}")
    if MAINSTREAM_HARD_RE.search(t):
        return True
    stars = {m.group(0).lower() for m in MEGASTAR_RE.finditer(t)}
    need = 3 if INDIE_SIGNAL_RE.search(t) else 2
    return len(stars) >= need

def contact_tier(email, ig, link, playlist_name, description):
    has = is_valid_data(email) or is_valid_data(ig) or bool(link)
    if has and has_submission_intent(playlist_name, description):
        return "A"
    return "B" if has else "C"

# ----------------------------------------------------------------------------
# Scoring — Fit (genre match) · Quality (real/alive) · Reachability (in your lane)
# ----------------------------------------------------------------------------
def fit_score(keywords, name, description, artists_text=""):
    kws = [k.strip().lower() for k in re.split(r"[,\n]", keywords or "") if k.strip()]
    kws = [w for k in kws for w in k.split() if w not in ("submit", "submissions", "submission", "curator", "playlist", "playlists", "music")]
    if not kws:
        return 50
    blob = normalize_text(f"{name} {description} {artists_text}").lower()
    hits = sum(1 for k in set(kws) if k in blob)
    return int(min(100, 30 + 70 * hits / max(1, len(set(kws)))))

def quality_score(saves, track_count, description, prof):
    s = 0
    lo, hi = int(prof.get("saves_min", 1000)), int(prof.get("saves_max", 200000))
    if lo <= saves <= hi:
        s += 45
    elif saves > 0:
        s += 15
    if 15 <= track_count <= int(prof.get("dump_bin_tracks", 500)):
        s += 25
    elif track_count > 0:
        s += 5
    if description and len(normalize_text(description)) > 20:
        s += 15
    if saves > 0 and track_count > 0 and saves / max(1, track_count) > 20:
        s += 15  # real audience per track, not a dumping ground
    return int(min(100, s))

def reachability_bucket(median_plays, prof):
    """Proxy: how big are the artists already on this playlist? Uses the median
    per-track playcount from the scraped tracklist, banded relative to your lane
    thresholds in Settings. Listener counts per playlist are private — this is
    the honest, scrapable stand-in."""
    if median_plays <= 0:
        return "Unknown"
    lo, hi = int(prof.get("lane_min_plays", 50000)), int(prof.get("lane_max_plays", 5000000))
    if median_plays > 50_000_000:
        return "Skip (superstars)"
    if median_plays > hi:
        return "Stretch"
    if median_plays >= lo:
        return "In your lane"
    return "Below you"

# ----------------------------------------------------------------------------
# Gemini — language-judgment cleanup only (the deterministic normalize is free)
# ----------------------------------------------------------------------------
def resolve_gemini_model(client):
    try:
        cached = st.session_state.get("_gemini_model")
    except Exception:
        cached = None
    if cached:
        return cached
    candidates = [GEMINI_MODEL]
    try:
        discovered = []
        for m in client.models.list():
            name = (getattr(m, "name", "") or "").split("/")[-1]
            if "flash" in name and not any(x in name for x in ["image", "live", "tts", "audio", "embedding", "lite", "veo", "nano"]):
                discovered.append(name)
        candidates += sorted(set(discovered) - set(candidates), reverse=True)
    except Exception:
        pass
    for cand in candidates:
        try:
            client.models.generate_content(model=cand, contents="ok")
            st.session_state["_gemini_model"] = cand
            return cand
        except Exception as e:
            msg = str(e).lower()
            if "quota" in msg or "exhaust" in msg or "rate" in msg:
                st.session_state["_gemini_model"] = cand
                return cand
    return GEMINI_MODEL

def gemini_config(model):
    kwargs = {"response_mime_type": "application/json"}
    if str(model).startswith("gemini-3"):
        try:
            kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.LOW)
        except Exception:
            pass
    return genai_types.GenerateContentConfig(**kwargs)

def clean_with_gemini(client, model, owner_name, playlist_name):
    """Return (clean_owner, clean_playlist, err). Strips 'Official', ✅, promo noise,
    and decides whether the owner string is a person/brand vs. contact string."""
    prompt = (
        "You clean Spotify playlist metadata. Return ONLY JSON: "
        '{"owner": "...", "playlist": "..."}.\n'
        "Rules: keep the real curator/brand name, drop 'Official', ticks, emoji, promo words, "
        "and stylized fonts. If the owner string is an email or @handle, keep it exactly as-is. "
        "Keep the playlist title's meaning but remove emoji and spam.\n"
        f"Owner: {owner_name}\nPlaylist: {playlist_name}"
    )
    try:
        resp = client.models.generate_content(model=model, contents=prompt, config=gemini_config(model))
        data = json.loads(resp.text)
        return str(data.get("owner", owner_name)).strip() or owner_name, str(data.get("playlist", playlist_name)).strip() or playlist_name, None
    except Exception as e:
        return owner_name, playlist_name, str(e)

# ----------------------------------------------------------------------------
# Apify helper (ported)
# ----------------------------------------------------------------------------
def run_apify_and_poll(client, actor_id, run_input, total_targets, report, label, max_usd=None, max_items=None):
    """Start an actor with a HARD per-run spend cap (Apify aborts the run at the cap) and poll to completion."""
    from decimal import Decimal
    kwargs = {"run_input": run_input}
    if max_usd:
        kwargs["max_total_charge_usd"] = Decimal(str(round(float(max_usd), 2)))
    if max_items:
        kwargs["max_items"] = int(max_items)
    run_obj = client.actor(actor_id).start(**kwargs)
    run_id = run_obj.get("id") if isinstance(run_obj, dict) else getattr(run_obj, "id", None)
    dataset_id = run_obj.get("defaultDatasetId") if isinstance(run_obj, dict) else getattr(run_obj, "default_dataset_id", getattr(run_obj, "defaultDatasetId", ""))
    if not run_id or not dataset_id:
        return "FAILED (could not read run/dataset id from Apify)", dataset_id
    start_time, status = time.time(), "UNKNOWN"
    while True:
        run_info = client.run(run_id).get()
        status = run_info.get("status") if isinstance(run_info, dict) else getattr(run_info, "status", "UNKNOWN")
        dataset_info = client.dataset(dataset_id).get()
        item_count = 0 if not dataset_info else (dataset_info.get("itemCount", 0) if isinstance(dataset_info, dict) else getattr(dataset_info, "item_count", 0))
        elapsed = int(time.time() - start_time)
        progress_val = min(0.95, item_count / total_targets) if total_targets > 0 else 0.5
        report(progress_val, f"{label}: {item_count}/{total_targets} · {elapsed}s · {status}")
        if status in ["SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"]:
            break
        if elapsed > APIFY_POLL_TIMEOUT_SECS:
            status = "TIMED-OUT (gave up waiting — check the run in your Apify console)"
            break
        time.sleep(1.5)
    return status, dataset_id

# ----------------------------------------------------------------------------
# Backup ZIP + flash messages (ported)
# ----------------------------------------------------------------------------
MEMORY_FILES = [SEEN_PLAYLISTS_FILE, BLOCKED_OWNERS_FILE, SCRAPED_IGS_FILE, PROFILE_FILE]

def build_backup_zip(df):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(DB_FILE, df.drop(columns=UI_ONLY_COLS, errors="ignore").to_csv(index=False))
        for f in MEMORY_FILES:
            if os.path.exists(f):
                z.write(f, arcname=f)
    return buf.getvalue()

def restore_memory_from_zip(z, replace):
    names = set(z.namelist())
    for f in [SEEN_PLAYLISTS_FILE, BLOCKED_OWNERS_FILE, SCRAPED_IGS_FILE]:
        if f in names:
            try:
                incoming = set(str(i) for i in json.loads(z.read(f).decode("utf-8")))
            except Exception:
                continue
            save_json_set(incoming if replace else (load_json_set(f) | incoming), f)
    if PROFILE_FILE in names and (replace or not os.path.exists(PROFILE_FILE)):
        with open(PROFILE_FILE, "wb") as out:
            out.write(z.read(PROFILE_FILE))

def flash(level, msg):
    st.session_state["_flash"] = (level, msg)

def show_flash():
    if "_flash" in st.session_state:
        level, msg = st.session_state.pop("_flash")
        getattr(st, level, st.info)(msg)

# ----------------------------------------------------------------------------
# Actor adapter — tolerant to BOTH shortlisted actors' field names so the app
# works out of the box with either. Swap actor + style in Settings.
#   all-in-one       → khadinakbar/spotify-all-in-one-scraper (camelCase, `owner` object)
#   playlist-scraper → ScrapeArchitect-style (snake_case: playlist_description, owner_url…)
# ----------------------------------------------------------------------------
def build_search_input(style, keywords, per_kw, fetch_details=False):
    if style == "playlist-scraper":
        return {"searchMode": "keyword", "keywords": keywords, "maxResults": per_kw, "fetchDetails": bool(fetch_details), "trackLimit": 40 if fetch_details else 0}
    return {"searchQueries": keywords, "searchResultsPerType": per_kw,
            "maxResults": per_kw * len(keywords), "responseFormat": "concise"}

def build_url_input(style, urls):
    if style == "playlist-scraper":
        return {"searchMode": "url", "urls": urls, "fetchDetails": True, "trackLimit": 40}
    return {"spotifyUrls": urls, "responseFormat": "concise", "maxResults": len(urls)}

def _first(d, *keys, default=""):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default

def _playlist_id_from(url_or_uri):
    m = re.search(r'playlist[/:]([a-zA-Z0-9]{22})', str(url_or_uri or ""))
    return m.group(1) if m else str(url_or_uri or "").strip()

def _owner_bits(item):
    owner = _first(item, "owner", "playlist_owner", "ownerName", default="")
    name, oid, ourl = "", "", _first(item, "owner_url", "ownerUrl", default="")
    if isinstance(owner, dict):
        name = _first(owner, "name", "displayName", "display_name", "id", default="")
        oid = _first(owner, "id", "uri", "url", default="")
        ourl = ourl or _first(owner, "url", "uri", default="")
    else:
        name = str(owner or "")
    if not oid:
        oid = ourl or name
    m = re.search(r'user[/:]([^/?\s]+)', str(oid))
    oid = m.group(1) if m else str(oid).replace("spotify:user:", "").strip()
    if ourl.startswith("spotify:user:"):
        ourl = "https://open.spotify.com/user/" + ourl.split(":")[-1]
    if not ourl and oid:
        ourl = f"https://open.spotify.com/user/{oid}"
    return str(name).strip(), str(oid).strip(), str(ourl).strip()

def _tracks_summary(item):
    """Return (median_playcount, artists_text) from whatever tracklist shape came back."""
    tracks = _first(item, "tracks", default=None)
    if isinstance(tracks, dict):
        tracks = tracks.get("items")
    if not tracks and isinstance(item.get("content"), dict):
        tracks = item["content"].get("items")
    plays, artists, added = [], [], []
    for t in (tracks or [])[:60]:
        if not isinstance(t, dict):
            continue
        d = t.get("itemV2", {}).get("data", t) if isinstance(t.get("itemV2"), dict) else t
        aa = t.get("addedAt") if isinstance(t, dict) else None
        aa = aa.get("isoString") if isinstance(aa, dict) else (aa or t.get("added_at") if isinstance(t, dict) else None)
        if aa:
            added.append(str(aa))
        pc = _first(d, "playcount", "playCount", "play_count", default=None)
        try:
            if pc is not None and str(pc).strip():
                plays.append(int(float(str(pc).replace(",", ""))))
        except Exception:
            pass
        a = d.get("artists")
        if isinstance(a, dict):
            a = a.get("items", [])
        if isinstance(a, list):
            for x in a:
                if isinstance(x, dict):
                    nm = _first(x, "name", default="") or _first(x.get("profile", {}) if isinstance(x.get("profile"), dict) else {}, "name", default="")
                    if nm:
                        artists.append(str(nm))
                elif isinstance(x, str):
                    artists.append(x)
        elif isinstance(a, str):
            artists.append(a)
    med = int(median(plays)) if plays else 0
    last = pd.to_datetime(max(added), errors="coerce", utc=True).tz_localize(None) if added else pd.NaT
    return med, " ".join(dict.fromkeys(artists)), last

def normalize_playlist_item(item, keyword, prof):
    """Map one raw actor item → one schema row. Returns None if it's not a playlist."""
    if not isinstance(item, dict):
        return None
    typ = str(item.get("type", "playlist")).lower()
    if typ and typ != "playlist":
        return None
    url = _first(item, "url", "playlist_url", "uri", default="")
    pid = _first(item, "playlist_id", "id", default="") or _playlist_id_from(url)
    if url.startswith("spotify:playlist:"):
        url = "https://open.spotify.com/playlist/" + url.split(":")[-1]
    if not url and pid:
        url = f"https://open.spotify.com/playlist/{pid}"
    name = normalize_text(_first(item, "name", "playlist_title", "title", default=""))
    desc = str(_first(item, "description", "playlist_description", default="") or "")
    owner_name, owner_id, owner_url = _owner_bits(item)
    owner_name_n = normalize_text(owner_name)
    saves = safe_int(_first(item, "followers", "playlist_followers", "followerCount", default=0))
    if isinstance(_first(item, "followers", default=None), dict):
        saves = safe_int(_first(item["followers"], "total", "count", default=0))
    tracks_n = safe_int(_first(item, "totalTracks", "total_tracks", "trackCount", "totalCount", default=0))
    if not tracks_n and isinstance(item.get("content"), dict):
        tracks_n = safe_int(item["content"].get("totalCount", 0))
    med_plays, artists_text, last_added = _tracks_summary(item)

    c = sweep_contacts(owner_name_n, name, desc)
    tier = contact_tier(c["email"], c["instagram"], c["link"], name, desc)
    ctag = cost_tag_for(name, desc, c["link"]) or ("free" if tier in ("A", "B") else "unknown")
    return {
        "Playlist_ID": str(pid), "Playlist Name": name, "Playlist URL": url,
        "Owner Name": owner_name_n, "Owner_ID": owner_id, "Owner URL": owner_url,
        "Saves": saves, "Track Count": tracks_n, "Median Playcount": med_plays, "Last Added": last_added,
        "Description": normalize_text(desc)[:1500], "Keyword": keyword,
        "Contact Tier": tier, "Reachability": reachability_bucket(med_plays, prof), "Cost Tag": ctag,
        "Fit Score": fit_score(prof.get("keywords", ""), name, desc, artists_text),
        "Quality Score": quality_score(saves, tracks_n, desc, prof),
        "Email Address": c["email"] or "None Found", "Instagram": c["instagram"] or "None",
        "Submission Link": c["link"], "Contact Source": c["source"], "Contact Confidence": c["confidence"],
        "Is Editorial": owner_id.lower() == "spotify" or owner_name_n.lower() == "spotify",
        "Is Dump Bin": tracks_n > int(prof.get("dump_bin_tracks", 500)),
        "Invites Subs": bool(has_submission_intent(name, desc)),
        "Declined": bool(refuses_submissions(name, desc)),
        "Notes": "Curator says: no submissions — auto-declined." if refuses_submissions(name, desc) else "",
        "Added_To_DB": pd.Timestamp.now(),
    }

def propagate_sibling_contacts(df):
    """Owner-pivot: a curator often parks their contact on a sibling playlist called
    'Submit Your Music' / 'Contact'. Copy that contact to every playlist of the same
    owner that has none, tagged source=submit-playlist (inherits paid-link tags)."""
    updated = 0
    for oid, grp in df.groupby("Owner_ID"):
        if not is_valid_data(oid) or len(grp) < 2:
            continue
        donors = grp[(grp["Playlist Name"].str.contains(SUBMIT_PLAYLIST_NAME_RE, na=False))
                     & (grp["Email Address"].apply(is_valid_data) | grp["Instagram"].apply(is_valid_data) | (grp["Submission Link"].str.len() > 0))]
        if donors.empty:
            donors = grp[grp["Email Address"].apply(is_valid_data)]
        if donors.empty:
            continue
        d = donors.iloc[0]
        for i in grp.index:
            if i == d.name:
                continue
            changed = False
            if not is_valid_data(df.at[i, "Email Address"]) and is_valid_data(d["Email Address"]):
                df.at[i, "Email Address"] = d["Email Address"]; changed = True
            if not is_valid_data(df.at[i, "Instagram"]) and is_valid_data(d["Instagram"]):
                df.at[i, "Instagram"] = d["Instagram"]; changed = True
            if not df.at[i, "Submission Link"] and d["Submission Link"]:
                df.at[i, "Submission Link"] = d["Submission Link"]; changed = True
            if changed:
                df.at[i, "Contact Source"] = "submit-playlist"
                df.at[i, "Contact Confidence"] = max(safe_int(df.at[i, "Contact Confidence"]), 65)
                if d["Cost Tag"] == "paid-link":
                    df.at[i, "Cost Tag"] = "paid-link"
                elif df.at[i, "Cost Tag"] == "unknown":
                    df.at[i, "Cost Tag"] = "free"
                df.at[i, "Contact Tier"] = contact_tier(df.at[i, "Email Address"], df.at[i, "Instagram"],
                                                        df.at[i, "Submission Link"], df.at[i, "Playlist Name"], df.at[i, "Description"])
                updated += 1
    return df, updated

def core_discover(df, seen, blocked, token, prof, keywords, report, urls=None):
    """Run the actor (keyword search or URL list), normalize, filter, sweep contacts,
    owner-pivot. Mutates + returns df and a summary string. Raises on Apify failure."""
    from apify_client import ApifyClient
    client = ApifyClient(token)
    style = prof.get("actor_style", "playlist-scraper")
    actor = (prof.get("actor_id") or "").strip()
    if not actor:
        raise RuntimeError("No Apify actor set — paste the actor slug (e.g. owner/spotify-playlist-scraper) in Settings → Apify actor.")
    per_kw = int(prof.get("results_per_keyword", 20))
    if urls:
        run_input, total, label = build_url_input(style, urls), len(urls), "Scraping playlist URLs"
    else:
        run_input, total, label = build_search_input(style, keywords, per_kw, prof.get("fetch_details", False)), per_kw * len(keywords), "Searching Spotify"
    cap = float(prof.get("max_usd_per_run", 1.0) or 0)
    report(0.0, f"Starting {actor} (hard cap ${cap:.2f} this run)…")
    status, dataset_id = run_apify_and_poll(client, actor, run_input, total, report, label, max_usd=cap or None, max_items=total)
    if status != "SUCCEEDED":
        raise RuntimeError(f"Apify run ended with status: {status}")
    report(0.97, "Filtering, sweeping contacts, scoring…")

    raw_sample, rows = None, []
    existing = set(df["Playlist_ID"].astype(str))
    stats = {"seen": 0, "editorial": 0, "blocked": 0, "dup": 0, "kept": 0, "instrumental": 0, "mainstream": 0, "nocontact": 0}
    kw_label = ", ".join(keywords) if keywords else "url"
    for item in client.dataset(dataset_id).iterate_items():
        if raw_sample is None:
            raw_sample = item
        row = normalize_playlist_item(item, item.get("query", kw_label) if isinstance(item, dict) else kw_label, prof)
        if not row or not row["Playlist_ID"]:
            continue
        stats["seen"] += 1
        if row["Is Editorial"]:
            stats["editorial"] += 1; continue
        if not urls and prof.get("skip_instrumental", True) and looks_instrumental(row["Playlist Name"], row["Description"], prof.get("exclude_words", "")):
            stats["instrumental"] += 1; seen.add(row["Playlist_ID"]); continue
        if not urls and prof.get("skip_mainstream", True) and looks_mainstream(row["Playlist Name"], row["Description"]):
            stats["mainstream"] += 1; seen.add(row["Playlist_ID"]); continue
        if row["Owner_ID"] in blocked:
            stats["blocked"] += 1; continue
        if row["Playlist_ID"] in existing or row["Playlist_ID"] in seen:
            stats["dup"] += 1; seen.add(row["Playlist_ID"]); continue
        seen.add(row["Playlist_ID"]); existing.add(row["Playlist_ID"])
        rows.append(row); stats["kept"] += 1

    if rows:
        new_ids = {r["Playlist_ID"] for r in rows}
        df = ensure_schema(pd.concat([df, pd.DataFrame(rows)], ignore_index=True))
        df, sib = propagate_sibling_contacts(df)
        if not urls and not prof.get("keep_no_contact", True):
            drop = df.index[df["Playlist_ID"].isin(new_ids) & (df["Contact Tier"] == "C")]
            stats["nocontact"] = int(len(drop)); df = df.drop(index=drop).reset_index(drop=True); stats["kept"] -= stats["nocontact"]
        else:
            stats["nocontact"] = int((df["Playlist_ID"].isin(new_ids) & (df["Contact Tier"] == "C")).sum())
    else:
        sib = 0
    save_json_set(seen, SEEN_PLAYLISTS_FILE)
    st.session_state["_raw_sample"] = raw_sample
    contactable = sum(1 for r in rows if r["Contact Tier"] in ("A", "B"))
    nc = f"{stats['nocontact']} discarded (no contact)" if not prof.get("keep_no_contact", True) else f"{stats['nocontact']} with no contact yet (hidden by default in Playlists)"
    summary = (f"Kept {stats['kept']} new playlists: {contactable + sib} with a contact (+{sib} via sibling submit-playlists), {nc}. "
               f"Skipped {stats['instrumental']} instrumental/type-beat, {stats['mainstream']} mainstream/superstar, {stats['editorial']} editorial, {stats['dup']} already seen, {stats['blocked']} blocked.")
    return df, summary

def core_scrape_instagram(df, igs_set, token, report):
    """IG bio → email for playlists that have an @handle but no email. Ported from Wavy."""
    targets, idx_map = [], {}
    for idx in df.index:
        ig = str(df.loc[idx, "Instagram"])
        if is_valid_data(ig) and not is_valid_data(df.loc[idx, "Email Address"]) \
           and str(df.loc[idx, "IG Bio"]) == "Not Scanned":
            u = ig.split(",")[0].strip().rstrip("/").lower()
            if u in igs_set:
                continue
            uname = extract_ig_username(u).lower()
            if uname not in idx_map:
                idx_map[uname] = {"indices": [], "url": u}
                targets.append(uname)
            idx_map[uname]["indices"].append(idx)
    if not targets:
        return df, None
    report(0.0, f"Starting Apify job for {len(targets)} Instagram profiles…")
    from apify_client import ApifyClient
    client = ApifyClient(token)
    status, dataset_id = run_apify_and_poll(client, "apify/instagram-profile-scraper", {"usernames": targets}, len(targets), report, "Scraping Instagram")
    if status != "SUCCEEDED":
        raise RuntimeError(f"Apify run ended with status: {status}")
    emails_found, scraped = 0, 0
    for item in client.dataset(dataset_id).iterate_items():
        uname = str(item.get("username", "")).lower()
        if uname not in idx_map:
            continue
        bio = str(item.get("biography", ""))
        emails = extract_emails(bio)
        for k in ("public_email", "business_email"):
            if item.get(k):
                emails.append(item[k])
        clean = [e for e in emails if "example" not in e.lower()]
        for i in idx_map[uname]["indices"]:
            if i not in df.index:
                continue
            df.loc[i, "IG Bio"] = bio if bio.strip() else "Empty Bio"
            df.loc[i, "IG Followers"] = safe_int(item.get("followersCount", 0))
            if clean and not is_valid_data(df.loc[i, "Email Address"]):
                df.loc[i, "Email Address"] = sorted(set(clean))[0]
                df.loc[i, "Contact Source"] = "ig-bio"
                df.loc[i, "Contact Confidence"] = max(safe_int(df.loc[i, "Contact Confidence"]), 75)
                df.loc[i, "Contact Tier"] = contact_tier(df.loc[i, "Email Address"], df.loc[i, "Instagram"], df.loc[i, "Submission Link"], df.loc[i, "Playlist Name"], df.loc[i, "Description"])
                if df.loc[i, "Cost Tag"] == "unknown":
                    df.loc[i, "Cost Tag"] = "free"
                emails_found += 1
        igs_set.add(idx_map[uname]["url"]); scraped += 1
    save_json_set(igs_set, SCRAPED_IGS_FILE)
    return df, f"Scraped {scraped} Instagram bios and found {emails_found} new emails."

# ----------------------------------------------------------------------------
# Pipeline helpers
# ----------------------------------------------------------------------------
def has_contact(row):
    return is_valid_data(row["Email Address"]) or is_valid_data(row["Instagram"]) or bool(str(row["Submission Link"]).strip())

def in_bands(row, prof):
    s = safe_int(row["Saves"])
    ok_saves = (s == 0) or (int(prof["saves_min"]) <= s <= int(prof["saves_max"]))
    ok_lane = row["Reachability"] in ("In your lane", "Stretch", "Unknown")
    return ok_saves and ok_lane and not row["Is Dump Bin"] and not row["Is Editorial"]

def curator_queue(df, prof):
    """One row per curator: the best-quality contactable, drafted, unpitched playlist per Owner_ID."""
    d = df[df.apply(has_contact, axis=1) & (df["Draft Pitch"].str.strip() != "") & ~df["Pitched"] & ~df["Declined"]].copy()
    if d.empty:
        return d
    d["_lane"] = d["Reachability"].map({"In your lane": 3, "Stretch": 2, "Unknown": 1}).fillna(0)
    d["_tier"] = d["Contact Tier"].map({"A": 2, "B": 1}).fillna(0)
    d = d.sort_values(["_tier", "_lane", "Quality Score", "Saves"], ascending=False)
    pitched_owners = set(df.loc[df["Pitched"], "Owner_ID"].astype(str))
    d = d[~d["Owner_ID"].astype(str).isin(pitched_owners)]
    key = d["Owner_ID"].where(d["Owner_ID"].apply(is_valid_data), d["Playlist_ID"])
    return d.loc[~key.duplicated()].drop(columns=["_lane", "_tier"])

def pitched_today(df):
    today = pd.Timestamp.now().normalize()
    return int((df["Pitch_Date"].dt.normalize() == today).sum())

def follow_ups_due(df):
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=FOLLOW_UP_DAYS)
    return df[df["Pitched"] & ~df["Replied"] & ~df["Followed Up"] & ~df["Declined"] & (df["Pitch_Date"] <= cutoff)]

def pipeline_stats(df):
    base = df[~df["Is Editorial"]] if not df.empty else df
    return {
        "total": len(base),
        "qualified": int(base.apply(lambda r: in_bands(r, PROF), axis=1).sum()) if len(base) else 0,
        "contactable": int(base.apply(has_contact, axis=1).sum()) if len(base) else 0,
        "drafted": int((base["Draft Pitch"].str.strip() != "").sum()) if len(base) else 0,
        "pitched": int(base["Pitched"].sum()) if len(base) else 0,
        "replied": int(base["Replied"].sum()) if len(base) else 0,
        "added": int(base["Added"].sum()) if len(base) else 0,
        "curators": int(base.loc[base["Owner_ID"].apply(is_valid_data), "Owner_ID"].nunique()) if len(base) else 0,
    }

def merge_pitch_csv(df, incoming):
    incoming = incoming.copy()
    incoming.columns = [str(c).strip() for c in incoming.columns]
    colmap = {c.lower(): c for c in incoming.columns}
    msg_col = next((colmap[k] for k in ["draft pitch", "pitch", "draft message", "message"] if k in colmap), None)
    if not msg_col:
        raise ValueError("Couldn't find a 'Draft Pitch' column in that CSV.")
    id_col = colmap.get("playlist_id")
    updated, skipped = 0, 0
    for _, r in incoming.iterrows():
        text = str(r[msg_col]).strip()
        if not text or text.lower() == "nan":
            continue
        mask = (df["Playlist_ID"].astype(str) == str(r[id_col]).strip()) if id_col else None
        if mask is not None and mask.any():
            df.loc[mask, "Draft Pitch"] = text
            df.loc[mask, "🔄 Regenerate"] = False
            updated += int(mask.sum())
        else:
            skipped += 1
    return df, updated, skipped

# ----------------------------------------------------------------------------
# Session boot — restore from GitHub on a fresh container, then load everything
# ----------------------------------------------------------------------------
cloud_restore_on_boot()
if "df" not in st.session_state:
    st.session_state.df = load_db()
PROF = load_profile()
seen_playlists = load_json_set(SEEN_PLAYLISTS_FILE)
blocked_owners = load_json_set(BLOCKED_OWNERS_FILE)
scraped_igs = load_json_set(SCRAPED_IGS_FILE)

# ----------------------------------------------------------------------------
# Styling (theme lives in .streamlit/config.toml)
# ----------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=block');
span[data-testid="stIconMaterial"], [class*="material-symbols"] {
    font-family: 'Material Symbols Rounded' !important; font-weight: normal !important; font-style: normal;
    letter-spacing: normal; text-transform: none; line-height: 1; -webkit-font-feature-settings: 'liga';
    font-feature-settings: 'liga'; font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
}
p, li, label, input, textarea, [data-testid="stMarkdownContainer"] p { font-family: 'Inter', -apple-system, sans-serif; }
h1, h2, h3, [data-testid="stMetricValue"] { font-family: 'Space Grotesk', sans-serif !important; letter-spacing: 0.01em; }
h1 { text-transform: uppercase; letter-spacing: 0.03em; }
.stButton button, .stDownloadButton button, .stLinkButton a, .stFormSubmitButton button { font-family: 'Space Grotesk', sans-serif !important; font-weight: 600; }
[data-testid="stCaptionContainer"], .stCaption, code, .stCode, [data-testid="stMetricLabel"] { font-family: 'IBM Plex Mono', monospace; }
.block-container {padding-top: 2.2rem; max-width: 1200px;}
[data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
</style>
""", unsafe_allow_html=True)

STAGE_COLORS = {
    "Discovered":  ("#9AA3B2", "#3E434D"),
    "Qualified":   ("#7FB3D5", "#2E4A63"),
    "Contactable": ("#37B8C4", "#14555C"),
    "Drafted":     ("#F0A93B", "#7A5210"),
    "Pitched":     ("#E8763A", "#77320F"),
    "Replied":     ("#3BC474", "#155A33"),
    "Added":       ("#EFC94C", "#6E5716"),
}

def vu_meter(label, count, total):
    pct = 0 if total <= 0 or count <= 0 else max(6, int(round(100 * count / total)))
    hi, lo = STAGE_COLORS.get(label, (ACCENT, "#7A5210"))
    return f"""
    <div style="text-align:center;">
      <div style="height:110px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.10);border-radius:8px;display:flex;align-items:flex-end;overflow:hidden;">
        <div style="width:100%;height:{pct}%;background:linear-gradient(180deg,{hi},{lo});"></div>
      </div>
      <div style="margin-top:6px;font-size:1.35rem;font-weight:700;font-variant-numeric:tabular-nums;">{count}</div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;letter-spacing:.12em;text-transform:uppercase;opacity:.6;">{label}</div>
    </div>"""

def profile_ready():
    return bool(PROF.get("artist_name") and PROF.get("track_link"))

# ============================================================================
# PAGE: Dashboard
# ============================================================================
def page_dashboard():
    show_flash()
    st.title("🎧 Wavy Pitch")
    st.caption("Find curators who put small artists on. Pitch them once, well. Track permanent placements.")
    df = st.session_state.df
    s = pipeline_stats(df)
    cols = st.columns(7)
    for col, (label, n) in zip(cols, [("Discovered", s["total"]), ("Qualified", s["qualified"]), ("Contactable", s["contactable"]),
                                       ("Drafted", s["drafted"]), ("Pitched", s["pitched"]), ("Replied", s["replied"]), ("Added", s["added"])]):
        col.markdown(vu_meter(label, n, max(1, s["total"])), unsafe_allow_html=True)

    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Curators found", s["curators"])
    m2.metric("Permanent placements", s["added"], help="Every add via direct outreach stays — this number only goes up.")
    m3.metric("Reply rate", f"{(s['replied'] / s['pitched'] * 100):.0f}%" if s["pitched"] else "—")
    m4.metric("Acceptance rate", f"{(s['added'] / s['pitched'] * 100):.0f}%" if s["pitched"] else "—")

    st.subheader("Next up")
    todo = []
    if not profile_ready():
        todo.append("⚙️ Fill in your artist name, genre and track link in **Settings** — pitches can't be written without them.")
    if not get_key("APIFY_API_TOKEN"):
        todo.append("🔑 Add your Apify token in **Settings** to start discovering playlists.")
    if s["total"] == 0:
        todo.append("🔎 Run your first keyword search in **Discover**.")
    ig_pending = int((df["Instagram"].apply(is_valid_data) & ~df["Email Address"].apply(is_valid_data) & (df["IG Bio"] == "Not Scanned")).sum()) if len(df) else 0
    if ig_pending:
        todo.append(f"📸 {ig_pending} playlists have an Instagram but no email — run the IG bio scrape in **Discover**.")
    need_pitch = int((df.apply(has_contact, axis=1) & (df["Draft Pitch"].str.strip() == "") & ~df["Pitched"]).sum()) if len(df) else 0
    if need_pitch:
        todo.append(f"✍️ {need_pitch} contactable playlists have no pitch yet — write a batch in **Write**.")
    q = curator_queue(df, PROF)
    if len(q):
        todo.append(f"📤 {len(q)} curators are ready to pitch in **Send**.")
    fu = follow_ups_due(df)
    if len(fu):
        todo.append(f"🔁 {len(fu)} pitches are {FOLLOW_UP_DAYS}+ days old with no reply — follow-ups due in **Send**.")
    invited = int((df["Invites Subs"] & ~df.apply(has_contact, axis=1) & ~df["Is Editorial"]).sum()) if len(df) else 0
    if invited:
        todo.append(f"🕵️ {invited} playlists **invite submissions** but hid the contact — prime manual-stalking targets, listed first under **Playlists → Needs manual look**.")
    for t in todo or ["✅ Nothing pending. Discover more keywords or check follow-ups."]:
        st.markdown(f"- {t}")

# ============================================================================
# PAGE: Discover
# ============================================================================
def page_discover():
    show_flash()
    st.title("🔎 Discover")
    st.caption("Keyword-search Spotify via Apify → drop editorial/dump-bin playlists → sweep every field for contacts → score. "
               "Contacts are found for free from what the search already returned; the only extra credits are the optional IG bio scrape.")
    token = get_key("APIFY_API_TOKEN")
    if not token:
        st.error("Apify token missing — add it in Settings.")

    with st.container(border=True):
        st.subheader("1 · Search by keywords")
        st.caption("One keyword per line. Mix genre/mood with submission-intent terms — "
                   "`trap submit`, `drill playlist submissions`, `melodic rap curator` — they bias the search toward playlists that *want* pitches.")
        kws_text = st.text_area("Keywords", value=PROF.get("keywords", ""), height=120, placeholder="trap submit\nmelodic rap submissions\nunderground hip hop curator")
        c1, c2 = st.columns([1, 3])
        per_kw = c1.number_input("Results per keyword", 5, 200, int(PROF.get("results_per_keyword", 20)), step=5,
                                 help="Start small, look at the contactable-rate the summary reports, then scale.")
        keywords = [k.strip() for k in kws_text.splitlines() if k.strip()]
        est = per_kw * len(keywords)
        cap = float(PROF.get("max_usd_per_run", 1.0))
        c2.caption(f"≈ {est} playlists this run · **hard cap ${cap:.2f}** (Apify stops the run at the cap — change in Settings). "
                   f"Fast mode returns description + owner (the contact fields) at the cheapest tier; saves/tracklists are fetched later, only for contactable playlists.")
        if PROF.get("actor_style") == "all-in-one":
            st.warning("The all-in-one actor bills ~6 entity types per keyword (artists, tracks, albums, shows…) and we only keep playlists. "
                       "A dedicated playlist scraper is far cheaper for this job — switch it in Settings.")
        if not (PROF.get("actor_id") or "").strip():
            st.error("No actor set — paste the actor slug in Settings → Apify actor (copy it from the Apify store page URL: apify.com/OWNER/ACTOR).")
        if st.button(f"Search Spotify ({len(keywords)} keywords)", type="primary", disabled=not (token and keywords)):
            PROF["keywords"], PROF["results_per_keyword"] = kws_text, int(per_kw)
            save_profile(PROF)
            _run_discover(token, keywords=keywords)

    with st.container(border=True):
        st.subheader("2 · Add playlists by URL (manual stalking)")
        st.caption("Found a playlist yourself — or a curator's *Submit Your Music* playlist? Paste the links. "
                   "They're scraped and merged; sibling contacts propagate to that owner's other playlists automatically.")
        urls_text = st.text_area("Spotify playlist URLs, one per line", height=80, key="manual_urls")
        urls = [u.strip() for u in urls_text.splitlines() if "spotify" in u]
        if st.button(f"Scrape {len(urls)} URL(s)", disabled=not (token and urls)):
            _run_discover(token, urls=urls)

    with st.container(border=True):
        st.subheader("3 · Fetch details (saves + tracklist) — only for contactable playlists")
        df = st.session_state.df
        need_det = df[df.apply(has_contact, axis=1) & (df["Saves"] == 0) & ~df["Is Editorial"]] if len(df) else df
        st.caption(f"{len(need_det)} contactable playlists have no saves/tracklist yet (fast mode skips them to save credits). "
                   "This pulls full details for ONLY those, so Quality and Reachability can be scored where it matters.")
        n_det = st.number_input("Max playlists this pass", 1, 200, min(25, max(1, len(need_det))), key="n_det")
        if st.button(f"Fetch details for {min(int(n_det), len(need_det))} playlists", disabled=not (token and len(need_det))):
            urls = need_det["Playlist URL"].head(int(n_det)).tolist()
            _run_details(token, urls)

    with st.container(border=True):
        st.subheader("4 · Enrich: Instagram bio → email")
        df = st.session_state.df
        pending = int((df["Instagram"].apply(is_valid_data) & ~df["Email Address"].apply(is_valid_data) & (df["IG Bio"] == "Not Scanned")).sum()) if len(df) else 0
        st.caption(f"{pending} playlists have an @handle but no email. Only these get scraped — nothing else costs a credit.")
        if st.button(f"Scrape {pending} Instagram bios", disabled=not (token and pending)):
            progress, status = st.progress(0.0), st.empty()
            def report(frac, text):
                progress.progress(min(1.0, max(0.0, float(frac)))); status.info(text)
            try:
                df2, summary = core_scrape_instagram(st.session_state.df, scraped_igs, token, report)
                st.session_state.df = df2
                persist(df2)
                flash("success", summary or "Nothing to scrape.")
                st.rerun()
            except Exception as e:
                status.error(f"Instagram scrape failed: {e}")

    if st.session_state.get("_raw_sample") is not None:
        with st.expander("🔬 Raw sample from the last actor run (for checking field names)"):
            st.json(st.session_state["_raw_sample"])

def _run_details(token, urls):
    """URL-mode detail fetch for already-known playlists: update saves / track count / median plays / reachability in place."""
    progress, status = st.progress(0.0), st.empty()
    def report(frac, text):
        progress.progress(min(1.0, max(0.0, float(frac)))); status.info(text)
    try:
        from apify_client import ApifyClient
        client = ApifyClient(token)
        style = PROF.get("actor_style", "playlist-scraper"); actor = (PROF.get("actor_id") or "").strip()
        if not actor:
            raise RuntimeError("No Apify actor set in Settings.")
        cap = float(PROF.get("max_usd_per_run", 1.0) or 0)
        status_, ds = run_apify_and_poll(client, actor, build_url_input(style, urls), len(urls), report, "Fetching playlist details", max_usd=cap or None, max_items=len(urls))
        if status_ != "SUCCEEDED":
            raise RuntimeError(f"Apify run ended with status: {status_}")
        df, updated = st.session_state.df, 0
        for item in client.dataset(ds).iterate_items():
            row = normalize_playlist_item(item, "details", PROF)
            if not row: continue
            m = df["Playlist_ID"].astype(str) == row["Playlist_ID"]
            if not m.any(): continue
            for col in ["Saves", "Track Count", "Median Playcount", "Last Added", "Reachability", "Quality Score", "Is Dump Bin"]:
                df.loc[m, col] = row[col]
            if not is_valid_data(df.loc[m, "Description"].iloc[0]) and row["Description"]:
                df.loc[m, "Description"] = row["Description"]
            updated += int(m.sum())
        st.session_state.df = ensure_schema(df); persist(st.session_state.df)
        flash("success", f"Updated details for {updated} playlists."); st.rerun()
    except Exception as e:
        status.error(f"Detail fetch failed: {e}")

def _run_discover(token, keywords=None, urls=None):
    progress, status = st.progress(0.0), st.empty()
    def report(frac, text):
        progress.progress(min(1.0, max(0.0, float(frac)))); status.info(text)
    try:
        df2, summary = core_discover(st.session_state.df, seen_playlists, blocked_owners, token, PROF, keywords or [], report, urls=urls)
        st.session_state.df = df2
        persist(df2)
        flash("success", summary)
        st.rerun()
    except Exception as e:
        status.error(f"Discovery failed: {e}")
        if st.session_state.get("_raw_sample") is not None:
            st.json(st.session_state["_raw_sample"])

# ============================================================================
# PAGE: Write
# ============================================================================
def claude_prompt():
    return f"""I'm {PROF.get('artist_name') or '[ARTIST NAME]'}, an independent {PROF.get('genre') or '[GENRE]'} artist. {PROF.get('one_liner') or ''}
Track to pitch: {PROF.get('track_link') or '[TRACK LINK]'}
{('EPK / more: ' + PROF['epk_link']) if PROF.get('epk_link') else ''}

Attached is a CSV of Spotify playlist curators. For EVERY row, write ONE short pitch in the "Draft Pitch" column and return the full CSV unchanged otherwise.

RULES
- 3–5 sentences, plain text. No subject line, no emojis, no placeholders like [Name], no markdown.
- Open by naming THEIR playlist ("{{Playlist Name}}") and one specific, true thing about it from the Description or the artists on it. Never generic flattery.
- One sentence on why my track fits THAT playlist specifically.
- Include the track link once.
- If Channel is "DM", keep it under 60 words and casual. If "email", up to 100 words, a little more complete.
- If Cost Tag is "paid-link", do NOT offer to pay — just pitch normally.
- Sound like a real artist reaching out to someone whose taste they respect. Direct, warm, zero hype.
- Close with a low-pressure line (e.g. "no worries if it's not a fit").

Return the CSV with the Draft Pitch column filled, keeping Playlist_ID intact — it's how the app matches rows back."""

def page_write():
    show_flash()
    st.title("✍️ Write")
    st.caption("Optionally tidy names with Gemini, then write personalised pitches through Claude.ai — free, no API, you review every batch.")
    df = st.session_state.df
    if df.empty:
        st.info("No playlists yet. Run a search in **Discover** first."); return
    if not profile_ready():
        st.warning("Fill in your artist name and track link in **Settings** first — the pitch prompt needs them.")

    with st.container(border=True):
        st.subheader("1 · Clean names (Gemini, optional)")
        st.caption("Stylized unicode and emoji are already stripped for free. This pass handles the judgement calls: 'X Beats Official ✅' → 'X Beats', so curator dedupe is clean.")
        targets = [i for i in df.index if has_contact(df.loc[i]) and not df.loc[i, "Pitched"]]
        if st.button(f"Clean names ({len(targets)})", disabled=not targets):
            key = get_key("GEMINI_API_KEY")
            if not key:
                st.error("Gemini API key missing — add it in Settings.")
            else:
                try:
                    g = google_genai.Client(api_key=key)
                    model = resolve_gemini_model(g)
                    prog, stat, done, errs = st.progress(0.0), st.empty(), 0, 0
                    for pos, i in enumerate(targets):
                        stat.info(f"Cleaning {df.loc[i, 'Owner Name']} ({pos + 1}/{len(targets)})")
                        o, p, err = clean_with_gemini(g, model, df.loc[i, "Owner Name"], df.loc[i, "Playlist Name"])
                        if err:
                            errs += 1
                            if errs >= 3:
                                break
                        else:
                            st.session_state.df.loc[i, "Owner Name"], st.session_state.df.loc[i, "Playlist Name"], done = o, p, done + 1
                        prog.progress((pos + 1) / len(targets))
                    persist(st.session_state.df)
                    flash("success" if not errs else "warning", f"Cleaned {done} rows with {model}." + (f" {errs} failed." if errs else ""))
                    st.rerun()
                except Exception as e:
                    st.error(f"Gemini failed: {e}")

    with st.container(border=True):
        st.subheader("2 · Write pitches (Claude.ai — no API)")
        need = [i for i in df.index if has_contact(df.loc[i]) and not df.loc[i, "Pitched"] and not df.loc[i, "Is Editorial"]
                and (df.loc[i, "Draft Pitch"].strip() == "" or df.loc[i, "🔄 Regenerate"]) and in_bands(df.loc[i], PROF)]
        # one row per curator — the pitch is per person, not per playlist
        d = df.loc[need].sort_values(["Contact Tier", "Quality Score"], ascending=[True, False])
        key = d["Owner_ID"].where(d["Owner_ID"].apply(is_valid_data), d["Playlist_ID"])
        d = d.loc[~key.duplicated()]
        st.caption(f"{len(d)} curators pending (one pitch per curator, only playlists inside your saves + lane bands).")
        if len(d):
            n = st.number_input("Curators in this batch", 1, len(d), min(40, len(d)))
            batch = d.head(int(n))[["Playlist_ID", "Playlist Name", "Owner Name", "Saves", "Description", "Contact Tier", "Cost Tag"]].copy()
            batch["Channel"] = d.head(int(n)).apply(lambda r: "email" if is_valid_data(r["Email Address"]) else "DM", axis=1).values
            batch["Description"] = batch["Description"].str.slice(0, 400)
            batch["Draft Pitch"] = ""
            st.markdown("**Step A — download the batch**")
            st.download_button(f"📥 Download batch CSV ({len(batch)} curators)", batch.to_csv(index=False).encode("utf-8"), "wavy_pitch_batch.csv", "text/csv")
        st.markdown("**Step B — paste this prompt into Claude.ai with the CSV attached**")
        st.code(claude_prompt(), language=None, wrap_lines=True)
        st.markdown("**Step C — import the finished CSV**")
        up = st.file_uploader("Finished batch from Claude.ai", type=["csv"], key="pitch_import", label_visibility="collapsed")
        if up is not None and st.button("Import pitches", type="primary"):
            try:
                st.session_state.df, updated, skipped = merge_pitch_csv(st.session_state.df, pd.read_csv(up))
                persist(st.session_state.df)
                flash("success" if updated else "warning", f"Imported {updated} pitches — saved and synced, waiting in Send." + (f" {skipped} rows matched nothing." if skipped else ""))
                st.rerun()
            except Exception as e:
                st.error(f"Could not import that CSV: {e}")

# ============================================================================
# PAGE: Send
# ============================================================================
def _mark_pitched(idx, channel):
    df = st.session_state.df
    df.loc[idx, ["Pitched", "Pitch_Date", "Channel"]] = [True, pd.Timestamp.now(), channel]
    persist(df)

def page_send():
    show_flash()
    st.title("📤 Send")
    st.caption("One curator at a time, one pitch each. Paced so your inbox stays trusted.")
    df = st.session_state.df
    cap, today = int(PROF.get("daily_cap", 15)), pitched_today(df)
    st.progress(min(1.0, today / max(1, cap)), text=f"Sent today: {today} / {cap} (daily cap — change in Settings)")
    capped = today >= cap

    q = curator_queue(df, PROF)
    q = q[q.apply(lambda r: in_bands(r, PROF), axis=1)] if len(q) else q
    if q.empty:
        st.info("Nothing ready to pitch. Write pitches in **Write**, or loosen your bands in Settings.")
    else:
        pos = st.session_state.get("send_pos", 0) % len(q)
        row, idx = q.iloc[pos], q.index[pos]
        nl, nm, nr = st.columns([1, 10, 1], vertical_alignment="bottom")
        if nl.button("◀", key="prev"):
            st.session_state.send_pos = (pos - 1) % len(q); st.rerun()
        nm.markdown(f"**{pos + 1} / {len(q)}** · [{row['Playlist Name']}]({row['Playlist URL']}) · {safe_int(row['Saves']):,} saves · Tier {row['Contact Tier']} · {row['Reachability']} · {row['Cost Tag']}")
        if nr.button("▶", key="next"):
            st.session_state.send_pos = (pos + 1) % len(q); st.rerun()
        siblings = df[(df["Owner_ID"] == row["Owner_ID"]) & (df["Playlist_ID"] != row["Playlist_ID"])] if is_valid_data(row["Owner_ID"]) else df.iloc[0:0]
        st.caption(f"Curator: **{row['Owner Name']}** ({row['Owner URL']})" + (f" · also runs {len(siblings)} other playlist(s) you found — one pitch covers them all." if len(siblings) else ""))
        if row["Cost Tag"] == "paid-link":
            st.warning("This curator links to a paid submission service. Pitch anyway or skip — your call.")

        pitch = st.text_area("Pitch (editable — saves on send)", value=row["Draft Pitch"], height=200, key=f"pitch_{idx}")
        if pitch != row["Draft Pitch"]:
            st.session_state.df.loc[idx, "Draft Pitch"] = pitch
        c1, c2, c3 = st.columns(3)
        with c1:
            if is_valid_data(row["Email Address"]):
                subj = quote(f"Playlist submission — {PROF.get('artist_name', '')}")
                st.link_button(f"✉️ Open in Mail → {row['Email Address']}", f"mailto:{row['Email Address']}?subject={subj}&body={quote(pitch)}", width="stretch")
                if st.button("Mark emailed", key=f"em_{idx}", type="primary", disabled=capped, width="stretch"):
                    _mark_pitched(idx, "email"); flash("success", "Marked as pitched by email."); st.rerun()
            else:
                st.caption("No email on file.")
        with c2:
            if is_valid_data(row["Instagram"]):
                st.link_button(f"📸 Open Instagram", row["Instagram"], width="stretch")
                if st.button("Mark DM'd", key=f"dm_{idx}", type="primary", disabled=capped, width="stretch"):
                    _mark_pitched(idx, "DM"); flash("success", "Marked as pitched by DM."); st.rerun()
            elif row["Submission Link"]:
                st.link_button("🔗 Open submission link", row["Submission Link"], width="stretch")
                if st.button("Mark submitted", key=f"sl_{idx}", type="primary", disabled=capped, width="stretch"):
                    _mark_pitched(idx, "link"); flash("success", "Marked as submitted."); st.rerun()
        with c3:
            if st.button("Skip / decline this curator", key=f"dec_{idx}", width="stretch"):
                st.session_state.df.loc[idx, "Declined"] = True; persist(st.session_state.df); st.rerun()
        if capped:
            st.info("Daily cap reached — come back tomorrow. Spreading sends out protects your deliverability.")
        with st.expander("Copy-ready DM"):
            st.code(pitch, language=None, wrap_lines=True)

    st.divider()
    st.subheader(f"🔁 Follow-ups due ({FOLLOW_UP_DAYS}+ days, no reply)")
    fu = follow_ups_due(df)
    if fu.empty:
        st.caption("None due.")
    for i, r in fu.iterrows():
        a, b, c = st.columns([5, 2, 2])
        a.markdown(f"**{r['Owner Name']}** · {r['Playlist Name']} · pitched {r['Pitch_Date']:%d %b} via {r['Channel']}")
        if b.button("Followed up", key=f"fu_{i}"):
            st.session_state.df.loc[i, ["Followed Up", "FollowUp_Date"]] = [True, pd.Timestamp.now()]; persist(st.session_state.df); st.rerun()
        if c.button("Replied ✓", key=f"rp_{i}"):
            st.session_state.df.loc[i, "Replied"] = True; persist(st.session_state.df); st.rerun()

# ============================================================================
# PAGE: Playlists — the full table. Everything is editable; add, delete, block.
# ============================================================================
def page_playlists():
    show_flash()
    st.title("📋 Playlists")
    st.caption("Every field is yours to edit. Found a contact by hand? Type it in — it's tagged `manual` and ranks highest. Tick ❌ to delete a playlist, 🗑️ to block the curator (all their playlists, forever).")
    df = st.session_state.df

    f1, f2, f3, f4 = st.columns(4)
    tier = f1.radio("Contact", ["All", "A · open for subs", "B · has contact", "Needs manual look"], horizontal=False)
    lane = f2.radio("Reachability", ["All", "In your lane", "Stretch", "Skip (superstars)", "Below you", "Unknown"], horizontal=False)
    cost = f3.radio("Cost", ["All", "free", "paid-link", "unknown"], horizontal=False)
    stage = f4.radio("Pipeline", ["All", "Not pitched", "Pitched", "Replied", "Added", "Declined"], horizontal=False,
                     help="Declined includes curators whose description says 'no submissions' — auto-marked so they never enter the queue.")
    search = st.text_input("Search name / owner / description")
    h1, h2 = st.columns(2)
    hide_nc = h1.checkbox("Hide playlists with no contact", value=True, help="Tier C rows stay stored (a sibling 'Submit' playlist can still fill them) but are hidden here by default.")
    only_bands = h2.checkbox("Only inside my saves + lane bands", value=False)

    v = df.copy()
    if hide_nc and not tier.startswith("Needs"): v = v[v.apply(has_contact, axis=1)]
    if tier.startswith("A ·"): v = v[v["Contact Tier"] == "A"]
    elif tier.startswith("B ·"): v = v[v["Contact Tier"] == "B"]
    elif tier.startswith("Needs"):
        v = v[~v.apply(has_contact, axis=1) & ~v["Is Editorial"]].sort_values("Invites Subs", ascending=False)
        st.info(f"{int(v['Invites Subs'].sum())} of these INVITE submissions but hid the contact — start your manual stalking there (listed first).")
    if lane != "All": v = v[v["Reachability"] == lane]
    if cost != "All": v = v[v["Cost Tag"] == cost]
    if stage == "Not pitched": v = v[~v["Pitched"]]
    elif stage != "All": v = v[v[stage]]
    if search:
        s = search.lower()
        v = v[v["Playlist Name"].str.lower().str.contains(s, na=False) | v["Owner Name"].str.lower().str.contains(s, na=False) | v["Description"].str.lower().str.contains(s, na=False)]
    if only_bands: v = v[v.apply(lambda r: in_bands(r, PROF), axis=1)]
    st.caption(f"{len(v)} of {len(df)} playlists · {v.loc[v['Owner_ID'].apply(is_valid_data), 'Owner_ID'].nunique()} curators")

    show_cols = ["🗑️ Block", "❌ Remove", "Pitched", "Replied", "Added", "Playlist Name", "Owner Name", "Saves", "Track Count", "Last Added",
                 "Contact Tier", "Invites Subs", "Reachability", "Cost Tag", "Email Address", "Instagram", "Submission Link", "Contact Source",
                 "Quality Score", "Fit Score", "Draft Pitch", "🔄 Regenerate", "Notes", "Playlist URL", "Description"]
    edited = st.data_editor(
        v[show_cols], width="stretch", height=560, num_rows="fixed", key="pl_editor",
        column_config={
            "Playlist URL": st.column_config.LinkColumn(display_text="open"),
            "Instagram": st.column_config.LinkColumn(display_text="ig"),
            "Submission Link": st.column_config.LinkColumn(display_text="link"),
            "Saves": st.column_config.NumberColumn(format="%d"),
            "Contact Tier": st.column_config.SelectboxColumn(options=["A", "B", "C"]),
            "Reachability": st.column_config.SelectboxColumn(options=["In your lane", "Stretch", "Skip (superstars)", "Below you", "Unknown"]),
            "Cost Tag": st.column_config.SelectboxColumn(options=["free", "paid-link", "unknown"]),
            "Draft Pitch": st.column_config.TextColumn(width="large"),
            "Description": st.column_config.TextColumn(width="large"),
        },
    )
    if st.button("💾 Apply edits, deletes and blocks", type="primary"):
        blocked_now, removed = set(), 0
        for i in edited.index:
            if edited.at[i, "🗑️ Block"]:
                oid = str(df.at[i, "Owner_ID"])
                if is_valid_data(oid): blocked_now.add(oid)
            if edited.at[i, "❌ Remove"]:
                removed += 1
            for col in show_cols:
                if col in UI_ONLY_COLS: continue
                new, old = edited.at[i, col], df.at[i, col]
                blank = lambda v: v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ("", "nan", "None", "None Found")
                if blank(new) and blank(old):
                    continue
                if str(new) != str(old):
                    df.at[i, col] = new
                    if col in ("Email Address", "Instagram", "Submission Link"):
                        df.at[i, "Contact Source"], df.at[i, "Contact Confidence"] = "manual", 100
                        df.at[i, "Contact Tier"] = contact_tier(df.at[i, "Email Address"], df.at[i, "Instagram"], df.at[i, "Submission Link"], df.at[i, "Playlist Name"], df.at[i, "Description"])
                        if df.at[i, "Cost Tag"] == "unknown": df.at[i, "Cost Tag"] = "free"
                    if col == "Added" and new: df.at[i, "Added_Date"] = pd.Timestamp.now()
        drop = edited.index[edited["❌ Remove"]].tolist()
        if blocked_now:
            blocked_owners.update(blocked_now); save_json_set(blocked_owners, BLOCKED_OWNERS_FILE)
            drop += df.index[df["Owner_ID"].astype(str).isin(blocked_now)].tolist()
        df = df.drop(index=sorted(set(drop))).reset_index(drop=True)
        st.session_state.df = ensure_schema(df); persist(st.session_state.df)
        flash("success", f"Saved. Removed {removed} playlist(s), blocked {len(blocked_now)} curator(s) — synced to GitHub.")
        st.rerun()

    with st.expander("➕ Add a playlist by hand (no scrape)"):
        with st.form("manual_add"):
            a, b = st.columns(2)
            m_name = a.text_input("Playlist name*"); m_url = b.text_input("Playlist URL")
            m_owner = a.text_input("Curator name*"); m_ourl = b.text_input("Curator URL")
            m_saves = a.number_input("Saves", 0, step=100); m_tracks = b.number_input("Track count", 0, step=10)
            m_email = a.text_input("Email"); m_ig = b.text_input("Instagram URL or @handle")
            m_desc = st.text_area("Description / notes")
            if st.form_submit_button("Add playlist") and m_name and m_owner:
                ig = m_ig.strip()
                if ig and not ig.startswith("http"): ig = "https://instagram.com/" + ig.lstrip("@")
                pid = _playlist_id_from(m_url) or f"manual-{hashlib.md5((m_name + m_owner).encode()).hexdigest()[:10]}"
                oid = re.sub(r'.*user/', '', m_ourl).strip("/") if m_ourl else normalize_text(m_owner).lower()
                row = normalize_playlist_item({"name": m_name, "url": m_url, "id": pid, "description": m_desc, "owner": {"name": m_owner, "id": oid, "url": m_ourl},
                                               "followers": int(m_saves), "totalTracks": int(m_tracks)}, "manual", PROF)
                if m_email: row["Email Address"], row["Contact Source"], row["Contact Confidence"] = m_email.strip(), "manual", 100
                if ig: row["Instagram"] = ig; row["Contact Source"], row["Contact Confidence"] = "manual", 100
                row["Contact Tier"] = contact_tier(row["Email Address"], row["Instagram"], row["Submission Link"], m_name, m_desc)
                if row["Cost Tag"] == "unknown" and row["Contact Tier"] != "C": row["Cost Tag"] = "free"
                st.session_state.df = ensure_schema(pd.concat([st.session_state.df, pd.DataFrame([row])], ignore_index=True))
                seen_playlists.add(pid); save_json_set(seen_playlists, SEEN_PLAYLISTS_FILE)
                persist(st.session_state.df); flash("success", f"Added {m_name}."); st.rerun()

# ============================================================================
# PAGE: Settings
# ============================================================================
def page_settings():
    show_flash()
    st.title("⚙️ Settings")

    with st.container(border=True):
        st.subheader("You — the artist being pitched")
        a, b = st.columns(2)
        PROF["artist_name"] = a.text_input("Artist name*", PROF.get("artist_name", ""))
        PROF["genre"] = b.text_input("Genre*", PROF.get("genre", ""), placeholder="e.g. melodic trap")
        PROF["track_link"] = a.text_input("Track link to pitch*", PROF.get("track_link", ""))
        PROF["epk_link"] = b.text_input("EPK / link-in-bio (optional)", PROF.get("epk_link", ""))
        PROF["monthly_listeners"] = a.number_input("Your Spotify monthly listeners", 0, value=int(PROF.get("monthly_listeners", 1000)), step=100,
                                                   help="Sets what 'in your lane' means.")
        PROF["one_liner"] = st.text_input("One line about you / the track (goes into the pitch prompt)", PROF.get("one_liner", ""))

    with st.container(border=True):
        st.subheader("Targeting — sliders over already-scraped data (re-slicing costs nothing)")
        smin, smax = st.slider("Saves band", 0, 500000, (int(PROF["saves_min"]), int(PROF["saves_max"])), step=500,
                               help="Playlists must fall in this range to reach the pitch queue. The uncontested small curators often sit at 1k–10k — keep the floor low.")
        PROF["saves_min"], PROF["saves_max"] = smin, smax
        lmin, lmax = st.slider("'In your lane' — median per-track plays of artists already on the playlist", 1000, 50000000,
                               (int(PROF["lane_min_plays"]), int(PROF["lane_max_plays"])), step=1000,
                               help="Proxy for how big the artists on the playlist are. Above the top = Stretch; > 50M = superstar playlists (skipped).")
        PROF["lane_min_plays"], PROF["lane_max_plays"] = lmin, lmax
        c1, c2 = st.columns(2)
        PROF["dump_bin_tracks"] = c1.number_input("Flag as dump-bin above this many tracks", 50, 5000, int(PROF["dump_bin_tracks"]), step=50)
        PROF["daily_cap"] = c2.number_input("Daily send cap", 1, 200, int(PROF["daily_cap"]))

    with st.container(border=True):
        st.subheader("Apify actor")
        PROF["actor_id"] = st.text_input("Spotify actor slug (owner/actor-name)", PROF.get("actor_id", ""),
                                         placeholder="e.g. scrapearchitect/spotify-playlist-scraper",
                                         help="Open the actor on apify.com — the slug is the last two parts of the URL: apify.com/OWNER/ACTOR-NAME.")
        PROF["actor_style"] = st.selectbox("Actor style", ["playlist-scraper", "all-in-one"],
                                           index=0 if PROF.get("actor_style", "playlist-scraper") == "playlist-scraper" else 1,
                                           help="playlist-scraper = a dedicated Spotify PLAYLIST actor (returns only playlists — cheapest). "
                                                "all-in-one = khadinakbar's multi-type actor (bills ~6 entity types per keyword — avoid for search).")
        c1, c2, c3 = st.columns(3)
        PROF["max_usd_per_run"] = c1.number_input("Hard cap per run (USD)", 0.10, 50.0, float(PROF.get("max_usd_per_run", 1.0)), step=0.10,
                                                  help="Apify aborts the run when this is reached. Your real safety net.")
        PROF["results_per_keyword"] = c2.number_input("Results per keyword", 5, 200, int(PROF.get("results_per_keyword", 20)), step=5)
        PROF["fetch_details"] = c3.checkbox("Fetch full details during search", value=bool(PROF.get("fetch_details", False)),
                                            help="OFF (recommended) = fast mode: description + owner only, cheapest. Details are fetched later just for contactable playlists via Discover → step 3.")
        st.markdown("**What to skip at ingest**")
        d1, d2 = st.columns(2)
        PROF["skip_instrumental"] = d1.checkbox("Skip type-beat / instrumental playlists", value=bool(PROF.get("skip_instrumental", True)),
                                                help="Decided mainly by the playlist NAME ('Boom Bap Beats', 'Hip Hop Instrumentals'). Mixed playlists that mention vocals are kept.")
        PROF["skip_mainstream"] = d1.checkbox("Skip mainstream / superstar playlists", value=bool(PROF.get("skip_mainstream", True)),
                                              help="'Famous songs', 'Top 100', 'chart hits', or two-plus mega-stars (Drake, Eminem, Kanye…) in the text. Genre-taste names like Nujabes or Little Simz do NOT trigger it.")
        PROF["keep_no_contact"] = d2.checkbox("Store playlists with no contact (hidden by default)", value=bool(PROF.get("keep_no_contact", True)),
                                              help="Keeping them lets a sibling 'Submit Your Music' playlist from the same curator fill in the contact later. Untick to discard them outright.")
        PROF["exclude_words"] = st.text_input("Extra playlist-name words to skip (comma-separated)", PROF.get("exclude_words", ""))
        st.caption("Cost reality: Spotify actors need residential proxies, which Apify bills on top of the per-result price. "
                   "Fast mode + the hard cap keep this small. After a run, open **Discover → Raw sample** to check `playlist_description` / `owner` came back.")

    if st.button("💾 Save settings", type="primary"):
        save_profile(PROF); cloud_sync(); flash("success", "Settings saved and synced."); st.rerun()

    with st.container(border=True):
        st.subheader("API keys")
        st.caption("Set these as Streamlit secrets (see README). Session overrides below are temporary.")
        for name, label in KEY_NAMES.items():
            st.text_input(f"{label} — {name}", value=st.session_state.get(f"key_{name}", ""), type="password", key=f"key_{name}",
                          placeholder="using secret ✓" if get_secret(name) else "not set")
        st.caption("☁️ GitHub auto-save: " + ("**ON** → " + _cloud_repo() + " / " + _cloud_branch() if cloud_enabled() else "**OFF** — add GITHUB_TOKEN + GITHUB_REPO secrets."))

    with st.container(border=True):
        st.subheader("Blocked curators")
        if blocked_owners:
            for oid in sorted(blocked_owners):
                a, b = st.columns([6, 1])
                a.code(oid)
                if b.button("Unblock", key=f"ub_{oid}"):
                    blocked_owners.discard(oid); save_json_set(blocked_owners, BLOCKED_OWNERS_FILE); cloud_sync(); st.rerun()
        else:
            st.caption("None.")

    with st.container(border=True):
        st.subheader("Backup & restore")
        st.download_button("⬇️ Download backup (.zip)", build_backup_zip(st.session_state.df), f"wavy_pitch_backup_{time.strftime('%Y%m%d_%H%M')}.zip", "application/zip")
        up = st.file_uploader("Restore from a backup", type=["zip", "csv"])
        mode = st.radio("Restore mode", ["Merge with current", "Replace everything"], horizontal=True)
        if up is not None and st.button("Restore"):
            replace = mode.startswith("Replace")
            try:
                if up.name.endswith(".zip"):
                    with zipfile.ZipFile(up) as z:
                        incoming = ensure_schema(pd.read_csv(io.BytesIO(z.read(DB_FILE)))) if DB_FILE in z.namelist() else ensure_schema(pd.DataFrame())
                        restore_memory_from_zip(z, replace)
                else:
                    incoming = ensure_schema(pd.read_csv(up))
                cur = st.session_state.df
                merged = incoming if replace else ensure_schema(pd.concat([cur, incoming[~incoming["Playlist_ID"].isin(cur["Playlist_ID"])]], ignore_index=True))
                st.session_state.df = merged; persist(merged); flash("success", f"Restored {len(incoming)} playlists."); st.rerun()
            except Exception as e:
                st.error(f"Restore failed: {e}")
        st.divider()
        if st.checkbox("I understand this deletes every playlist, memory file and setting") and st.button("🧨 Factory reset", type="secondary"):
            for f in [DB_FILE] + MEMORY_FILES:
                if os.path.exists(f): os.remove(f)
            st.session_state.df = ensure_schema(pd.DataFrame()); cloud_sync(); flash("warning", "Reset complete."); st.rerun()

# ============================================================================
# Navigation
# ============================================================================
pg_dashboard = st.Page(page_dashboard, title="Dashboard", icon="📊", default=True)
pg_discover = st.Page(page_discover, title="Discover", icon="🔎")
pg_write = st.Page(page_write, title="Write", icon="✍️")
pg_send = st.Page(page_send, title="Send", icon="📤")
pg_playlists = st.Page(page_playlists, title="Playlists", icon="📋")
pg_settings = st.Page(page_settings, title="Settings", icon="⚙️")
nav = st.navigation({"Overview": [pg_dashboard], "Workflow": [pg_discover, pg_write, pg_send], "Data": [pg_playlists, pg_settings]})

with st.sidebar:
    s = pipeline_stats(st.session_state.df)
    st.divider()
    st.caption("🎧 **Wavy Pitch**")
    st.caption(f"{s['total']} playlists · {s['curators']} curators · {s['pitched']} pitched · {s['added']} added")
    st.caption(" · ".join(("🟢" if get_key(n) else "⚪") + " " + lbl for n, lbl in KEY_NAMES.items()))

nav.run()

# ----------------------------------------------------------------------------
# Keep scroll position across reruns (ported from Wavy Outreach)
# ----------------------------------------------------------------------------
try:
    components.html("""
<script>
(function () {
  try {
    var P = window.parent, D = P.document, KEY = "wavy_scroll_" + P.location.pathname;
    function pageEl() { return D.querySelector('section[data-testid="stMain"]') || D.querySelector('.main'); }
    function grids() { return Array.prototype.slice.call(D.querySelectorAll('.dvn-scroller')); }
    function load() { try { return JSON.parse(P.sessionStorage.getItem(KEY) || "{}"); } catch (e) { return {}; } }
    function save() {
      var pos = { win: [P.scrollX || 0, P.scrollY || 0] };
      var pe = pageEl(); if (pe) pos.page = [pe.scrollLeft || 0, pe.scrollTop || 0];
      var gs = grids(); for (var i = 0; i < gs.length; i++) pos["grid" + i] = [gs[i].scrollLeft, gs[i].scrollTop];
      try { P.sessionStorage.setItem(KEY, JSON.stringify(pos)); } catch (e) {}
    }
    var saveSoon; function saveDebounced() { clearTimeout(saveSoon); saveSoon = setTimeout(save, 120); }
    function restorePage(saved) {
      var pe = pageEl(); if (pe && saved.page) { pe.scrollLeft = saved.page[0]; pe.scrollTop = saved.page[1]; }
      if (saved.win && (saved.win[0] || saved.win[1])) P.scrollTo(saved.win[0], saved.win[1]);
    }
    function restoreGrids(saved, onlyIdx) {
      var gs = grids();
      for (var i = 0; i < gs.length; i++) {
        if (onlyIdx && onlyIdx.indexOf(i) === -1) continue;
        var p = saved["grid" + i]; if (p) { gs[i].scrollLeft = p[0]; gs[i].scrollTop = p[1]; }
      }
    }
    var bootAt = Date.now();
    setInterval(function () {
      var saved = load(), freshGrids = [], gs = grids();
      for (var i = 0; i < gs.length; i++) {
        if (!gs[i].dataset.wavyHook) { gs[i].dataset.wavyHook = "1"; gs[i].addEventListener("scroll", saveDebounced, { passive: true }); freshGrids.push(i); }
      }
      var pe = pageEl();
      if (pe && !pe.dataset.wavyHook) { pe.dataset.wavyHook = "1"; pe.addEventListener("scroll", saveDebounced, { passive: true }); if (Date.now() - bootAt < 4000) restorePage(saved); }
      if (!D.body.dataset.wavyWin) { D.body.dataset.wavyWin = "1"; P.addEventListener("scroll", saveDebounced, { passive: true }); }
      if (freshGrids.length) { restoreGrids(saved, Date.now() - bootAt < 4000 ? null : freshGrids); restorePage(saved); }
    }, 200);
  } catch (e) {}
})();
</script>
""", height=0)
except Exception:
    pass

# ----------------------------------------------------------------------------
# Cloud save — runs after every script run so nothing this interaction changed
# is left unsaved. (Mutations also call persist() immediately.)
# ----------------------------------------------------------------------------
if cloud_enabled():
    cloud_sync()

with st.sidebar:
    st.divider()
    if cloud_enabled():
        err, pending = st.session_state.get("_cloud_error"), cloud_pending_count()
        if err:
            st.caption(f"☁️⚠️ Cloud save error: {err}")
        elif pending:
            st.caption(f"☁️ {pending} change(s) waiting to save")
        else:
            ts = st.session_state.get("_cloud_last_sync")
            st.caption("☁️ All changes saved to GitHub" + (f" · {time.strftime('%H:%M', time.localtime(ts))}" if ts else ""))
        if st.button("💾 Save to cloud now", width="stretch"):
            n, e = cloud_sync()
            st.error(e) if e else st.toast("Saved to GitHub ✓" if n else "Already up to date ✓")
    else:
        st.caption("☁️ Cloud save is OFF — data is lost when the app reboots. Add GITHUB_TOKEN + GITHUB_REPO secrets.")
