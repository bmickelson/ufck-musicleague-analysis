"""Look up release year, length, cover art and genre for every submitted song.

Spotify's public embed page gives the length, album art, artist list and the
album's release date. MusicBrainz gives the earliest known release of the
recording (so remasters and compilations don't count as new) and community
tags on the artist, which we use for genre.

Results are cached in cache/*.json, so the script can be stopped and rerun.
MusicBrainz allows about one request per second, so a full run takes a while.
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(ROOT, "cache")
UA = "MusicLeagueStats/0.1 (personal stats page; python-urllib)"


def load_cache(name):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(name, obj):
    os.makedirs(CACHE, exist_ok=True)
    tmp = os.path.join(CACHE, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(CACHE, name))


def all_submissions():
    subs = []
    for fn in sorted(os.listdir(DATA)):
        if not fn.endswith(".zip"):
            continue
        with zipfile.ZipFile(os.path.join(DATA, fn)) as z:
            text = z.read("submissions.csv").decode("utf-8-sig")
        subs.extend(csv.DictReader(io.StringIO(text)))
    return subs


def get(url, tries=4, ua=UA):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json" if ua == UA else "text/html"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 + 3 * i)
        except Exception:
            time.sleep(2 + 3 * i)
    return None


# ---------- Spotify embed ----------

def spotify_track(track_id):
    html = get(f"https://open.spotify.com/embed/track/{track_id}", ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")
    if not html:
        return None
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        e = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, ValueError):
        return None
    image = None
    imgs = (e.get("visualIdentity") or {}).get("image") or []
    if imgs:
        image = sorted(imgs, key=lambda i: i.get("maxWidth") or 0)[0].get("url")
        mid = [i for i in imgs if (i.get("maxWidth") or 0) >= 200]
        if mid:
            image = sorted(mid, key=lambda i: i.get("maxWidth"))[0]["url"]
    rd = (e.get("releaseDate") or {}).get("isoString")
    return {
        "artists": [a["name"] for a in e.get("artists") or []],
        "year": int(rd[:4]) if rd else None,
        "ms": e.get("duration"),
        "image": image,
    }


# ---------- MusicBrainz ----------

_last = [0.0]


def mb(path, params):
    wait = 1.1 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    params = dict(params, fmt="json")
    body = get(f"https://musicbrainz.org/ws/2/{path}?" + urllib.parse.urlencode(params))
    return json.loads(body) if body else None


def clean_title(t):
    t = re.sub(r"\s+-\s+.*$", "", t)  # "Song - Remastered 2009", "Song - Live"
    t = re.sub(r"\s*[\(\[][^)\]]*(remaster|version|live|mono|stereo|edit|mix|feat|with |from )[^)\]]*[\)\]]", "", t, flags=re.I)
    return t.strip() or t


def lucene(s):
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", s)


def norm(s):
    s = s.lower().replace("&", "and")
    s = re.sub(r"^the\s+", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def mb_recording_year(title, artist):
    q = f'recording:"{lucene(clean_title(title))}" AND artist:"{lucene(artist)}" AND firstreleasedate:[1000 TO 3000]'
    res = mb("recording", {"query": q, "limit": 25})
    if not res:
        return None
    years = []
    want_t, want_a = norm(clean_title(title)), norm(artist)
    for r in res.get("recordings", []):
        if r.get("score", 0) < 80:
            continue
        if norm(r.get("title", "")) != want_t:
            continue
        credit = "".join(c.get("name", "") + c.get("joinphrase", "") for c in r.get("artist-credit", []))
        if want_a not in norm(credit):
            continue
        d = r.get("first-release-date") or ""
        if re.match(r"^\d{4}", d):
            years.append(int(d[:4]))
    return min(years) if years else None


def mb_artist_tags(name):
    res = mb("artist", {"query": f'artist:"{lucene(name)}"', "limit": 5})
    if not res:
        return None
    want = norm(name)
    best = None
    for a in res.get("artists", []):
        if norm(a.get("name", "")) == want or any(norm(x.get("name", "")) == want for x in a.get("aliases", []) or []):
            if best is None or len(a.get("tags", [])) > len(best.get("tags", [])):
                best = a
            if a.get("score", 0) == 100 and a.get("tags"):
                best = a
                break
    if not best:
        return {}
    return {t["name"]: t.get("count", 1) for t in best.get("tags", [])}


def main():
    subs = all_submissions()
    tracks = {}
    for s in subs:
        tid = s["Spotify URI"].split(":")[-1]
        tracks.setdefault(tid, (s["Title"], s["Artist(s)"]))
    print(f"{len(tracks)} unique tracks", flush=True)

    # Spotify, a few at a time
    sp = load_cache("spotify.json")
    todo = [t for t in tracks if sp.get(t) is None]
    print(f"spotify: {len(todo)} to fetch", flush=True)
    with ThreadPoolExecutor(6) as ex:
        for i, (tid, info) in enumerate(zip(todo, ex.map(spotify_track, todo))):
            sp[tid] = info
            if i % 100 == 99:
                save_cache("spotify.json", sp)
                print(f"  spotify {i + 1}/{len(todo)}", flush=True)
    save_cache("spotify.json", sp)

    def lead_artist(tid):
        info = sp.get(tid)
        if info and info.get("artists"):
            return info["artists"][0]
        return tracks[tid][1].split(",")[0].strip()

    # MusicBrainz artists (genres) first: fewer requests, more value
    ar = load_cache("mb_artists.json")
    names = sorted({lead_artist(t) for t in tracks} - set(ar))
    print(f"musicbrainz artists: {len(names)} to fetch", flush=True)
    for i, n in enumerate(names):
        tags = mb_artist_tags(n)
        if tags is not None:
            ar[n] = tags
        if i % 50 == 49:
            save_cache("mb_artists.json", ar)
            print(f"  artists {i + 1}/{len(names)}", flush=True)
    save_cache("mb_artists.json", ar)

    rec = load_cache("mb_recordings.json")
    todo = [t for t in tracks if t not in rec]
    print(f"musicbrainz recordings: {len(todo)} to fetch", flush=True)
    for i, tid in enumerate(todo):
        title = tracks[tid][0]
        year = mb_recording_year(title, lead_artist(tid))
        rec[tid] = year
        if i % 50 == 49:
            save_cache("mb_recordings.json", rec)
            print(f"  recordings {i + 1}/{len(todo)}", flush=True)
    save_cache("mb_recordings.json", rec)
    print("done", flush=True)


if __name__ == "__main__":
    sys.exit(main())
