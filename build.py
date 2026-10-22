"""Turn the Music League exports in data/ into one stats page at docs/index.html.

    python3 build.py

League names aren't in the exports, so leagues.json maps each league id (the
zip's file name) to a display name and a turbo flag, which drives the page's
All / Turbo / Regular filter. New leagues get placeholder names; edit and rebuild. Song years, lengths and genres come from cache/ (see enrich.py);
anything missing there is just left out of those sections.
"""
import collections
import csv
import io
import json
import os
import statistics
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(ROOT, "cache")
OUT = os.path.join(ROOT, "docs", "index.html")
TEMPLATE = os.path.join(ROOT, "template.html")
LEAGUES_FILE = os.path.join(ROOT, "leagues.json")
SITE_FILE = os.path.join(ROOT, "site.json")

MIN_SONGS = 15  # minimum songs for per-song averages

# Broad genre buckets, matched in order against MusicBrainz artist tags.
GENRES = [
    ("Hip hop", ["hip hop", "hip-hop", "rap", "trap", "boom bap", "grime", "drill"]),
    ("Metal", ["metal", "metalcore", "grindcore", "deathcore", "djent"]),
    ("Punk", ["punk", "hardcore", "emo", "ska", "riot grrrl"]),
    ("Electronic / dance", ["electronic", "electronica", "house", "techno", "edm", "dance", "disco", "trance",
                            "drum and bass", "dubstep", "synth-pop", "synthpop", "ambient", "idm", "trip hop",
                            "big beat", "electro"]),
    ("R&B / soul", ["soul", "r&b", "rnb", "rhythm and blues", "rhythm & blues", "funk", "motown", "neo soul",
                    "neo-soul", "quiet storm", "new jack swing", "gospel"]),
    ("Jazz / blues", ["jazz", "blues", "swing", "bebop", "big band", "bossa nova", "vocal jazz", "fusion"]),
    ("Country / Americana", ["country", "americana", "bluegrass", "alt-country", "outlaw country", "honky tonk"]),
    ("Latin / reggae", ["latin", "reggae", "reggaeton", "salsa", "dancehall", "cumbia", "bachata", "dub",
                        "afrobeat", "afrobeats", "bossa", "samba", "mpb", "tropical"]),
    ("Folk / singer-songwriter", ["folk", "singer-songwriter", "singer/songwriter", "acoustic", "indie folk",
                                 "folk rock"]),
    ("Indie / alternative", ["indie", "alternative", "alternative rock", "indie rock", "post-punk", "shoegaze",
                             "new wave", "grunge", "britpop", "dream pop", "lo-fi", "post-rock", "noise rock",
                             "art rock", "emo"]),
    ("Pop", ["pop", "teen pop", "dance-pop", "electropop", "k-pop", "adult contemporary"]),
    ("Rock", ["rock", "hard rock", "classic rock", "blues rock", "rock and roll", "rock & roll", "psychedelic rock",
              "progressive rock", "glam rock", "southern rock", "garage rock", "soft rock", "heartland rock"]),
    ("Classical / soundtrack", ["classical", "soundtrack", "score", "orchestral", "opera", "baroque", "musical",
                                "show tunes", "broadway", "film score", "video game music"]),
]


def genre_for(tags):
    """Pick the bucket whose matching tags carry the most votes."""
    if not tags:
        return None
    scores = collections.Counter()
    for tag, count in tags.items():
        t = tag.lower()
        weight = max(count, 0) + 1
        for bucket, keys in GENRES:
            if t in keys or any(k == t or (len(k) > 3 and k in t.split()) for k in keys):
                scores[bucket] += weight
                break
            if any(k in t for k in keys if len(k) > 4):
                scores[bucket] += weight * 0.5
                break
    if not scores:
        return None
    return scores.most_common(1)[0][0]


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def read_league(path):
    with zipfile.ZipFile(path) as z:
        def table(name):
            return list(csv.DictReader(io.StringIO(z.read(name + ".csv").decode("utf-8-sig"))))
        return {k: table(k) for k in ("competitors", "rounds", "submissions", "votes")}


def main():
    raw = {}
    for fn in sorted(os.listdir(DATA)):
        if fn.endswith(".zip"):
            raw[fn[:-4]] = read_league(os.path.join(DATA, fn))

    # Order leagues by when the first song was submitted
    def start(lid):
        return min((s["Created"] for s in raw[lid]["submissions"]), default="9999")

    order = sorted(raw, key=start)
    names_cfg = load_json(LEAGUES_FILE, {})
    changed = False
    for i, lid in enumerate(order):
        if lid not in names_cfg:
            first = datetime.fromisoformat(start(lid).replace("Z", "+00:00"))
            names_cfg[lid] = {"name": f"League {i + 1} · {first.strftime('%b %Y')}", "turbo": False}
            changed = True
    for lid, v in names_cfg.items():  # older files held just the name
        if isinstance(v, str):
            names_cfg[lid] = {"name": v, "turbo": False}
            changed = True
    if changed:
        with open(LEAGUES_FILE, "w", encoding="utf-8") as f:
            json.dump({k: names_cfg[k] for k in order}, f, indent=2, ensure_ascii=False)
    site_cfg = load_json(SITE_FILE, {"title": "Heavy Rotation", "group": "Our Music Leagues",
                                      "tagline": "Winners, flops, repeat offenders, and the people who vote the hardest."})
    if not os.path.exists(SITE_FILE):
        with open(SITE_FILE, "w", encoding="utf-8") as f:
            json.dump(site_cfg, f, indent=2)

    spotify = load_json(os.path.join(CACHE, "spotify.json"), {})
    mb_rec = load_json(os.path.join(CACHE, "mb_recordings.json"), {})
    mb_art = load_json(os.path.join(CACHE, "mb_artists.json"), {})

    # ---------- players ----------
    pname = {}
    for lid in order:  # later leagues win, so the newest name sticks
        for c in raw[lid]["competitors"]:
            pname[c["ID"]] = c["Name"].strip()

    # ---------- songs, rounds, scores ----------
    leagues, rounds, subs, votes = [], [], [], []
    for li, lid in enumerate(order):
        L = raw[lid]
        vs = L["votes"]
        pts = [int(v["Points Assigned"]) for v in vs]
        up_cap = max([p for p in pts if p > 0], default=0)
        down_cap = min([p for p in pts if p < 0], default=0)
        sub_by_round = collections.defaultdict(list)
        for s in L["submissions"]:
            sub_by_round[s["Round ID"]].append(s)
        round_rows = [r for r in L["rounds"] if sub_by_round.get(r["ID"])]
        round_rows.sort(key=lambda r: min(s["Created"] for s in sub_by_round[r["ID"]]))
        leagues.append({
            "id": lid, "i": li, "name": names_cfg[lid]["name"].strip(), "turbo": bool(names_cfg[lid].get("turbo")),
            "start": start(lid)[:10],
            "end": max((v["Created"] for v in vs), default=start(lid))[:10],
            "players": sorted({c["ID"] for c in L["competitors"]}),
            "up": up_cap, "down": down_cap,
            "rounds": [],
        })
        for ri, r in enumerate(round_rows):
            rid = r["ID"]
            rvotes = [v for v in vs if v["Round ID"] == rid]
            voters = {v["Voter ID"] for v in rvotes}
            rounds.append({"id": rid, "league": li, "n": ri + 1, "name": r["Name"].strip(),
                           "desc": r["Description"].strip(), "playlist": r["Playlist URL"],
                           "voters": len(voters)})
            leagues[-1]["rounds"].append(len(rounds) - 1)
            for s in sub_by_round[rid]:
                uri = s["Spotify URI"]
                sv = [v for v in rvotes if v["Spotify URI"] == uri]
                voted = s["Submitter ID"] in voters
                kept = [v for v in sv if voted or int(v["Points Assigned"]) < 0]
                tid = uri.split(":")[-1]
                sp = spotify.get(tid) or {}
                artists = sp.get("artists") or [a.strip() for a in s["Artist(s)"].split(",")]
                lead = artists[0] if artists else s["Artist(s)"]
                years = [y for y in (sp.get("year"), mb_rec.get(tid)) if y]
                subs.append({
                    "round": len(rounds) - 1, "league": li, "tid": tid,
                    "title": s["Title"].strip(), "artist": s["Artist(s)"].strip(), "lead": lead,
                    "by": s["Submitter ID"],
                    "score": sum(int(v["Points Assigned"]) for v in kept),
                    "lost": 0 if voted else sum(int(v["Points Assigned"]) for v in sv if int(v["Points Assigned"]) > 0),
                    "voted": voted,
                    "up": sum(int(v["Points Assigned"]) for v in kept if int(v["Points Assigned"]) > 0),
                    "upvoters": len({v["Voter ID"] for v in kept if int(v["Points Assigned"]) > 0}),
                    "downvoters": len({v["Voter ID"] for v in kept if int(v["Points Assigned"]) < 0}),
                    "year": min(years) if years else None,
                    "ms": sp.get("ms"),
                    "img": sp.get("image"),
                    "genre": genre_for(mb_art.get(lead)),
                })
            for v in rvotes:
                p = int(v["Points Assigned"])
                target = next((x for x in subs[-len(sub_by_round[rid]):] if x["tid"] == v["Spotify URI"].split(":")[-1]), None)
                votes.append({"round": len(rounds) - 1, "league": li, "voter": v["Voter ID"], "pts": p,
                              "to": target["by"] if target else None, "comment": bool(v["Comment"].strip())})

    # rank songs within each round (ties share the better rank)
    by_round = collections.defaultdict(list)
    for i, s in enumerate(subs):
        by_round[s["round"]].append(i)
    for rid, idxs in by_round.items():
        scores = sorted((subs[i]["score"] for i in idxs), reverse=True)
        for i in idxs:
            subs[i]["rank"] = scores.index(subs[i]["score"]) + 1
            subs[i]["last"] = subs[i]["score"] == scores[-1] and len(scores) > 1
            subs[i]["of"] = len(idxs)
            subs[i]["tied"] = scores.count(subs[i]["score"]) > 1
        rounds[rid]["avg"] = round(statistics.mean(scores), 1)

    # league standings
    for L in leagues:
        tot = collections.Counter()
        for s in subs:
            if s["league"] == L["i"]:
                tot[s["by"]] += s["score"]
        for p in L["players"]:
            tot.setdefault(p, 0)
        L["standings"] = [[p, t] for p, t in tot.most_common()]

    # players become small integers to keep the page light
    pids = sorted(pname, key=lambda p: pname[p].lower())
    px = {p: i for i, p in enumerate(pids)}
    for s in subs:
        s["by"] = px[s["by"]]
    for L in leagues:
        L["players"] = [px[p] for p in L["players"]]
        L["standings"] = [[px[p], t] for p, t in L["standings"]]

    data = {
        "site": site_cfg,
        "built": datetime.now().strftime("%B %-d, %Y"),
        "players": [pname[p] for p in pids],
        "leagues": leagues,
        "rounds": rounds,
        "subs": subs,
        # [round, voter, points, submitter]
        "votes": [[v["round"], px[v["voter"]], v["pts"], px[v["to"]] if v["to"] else -1]
                  for v in votes if v["pts"] != 0],
        "genres": [g for g, _ in GENRES],
        "minSongs": MIN_SONGS,
    }
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html.replace("/*__DATA__*/null", blob))
    enriched = sum(1 for s in subs if s["year"])
    print(f"{len(leagues)} leagues, {len(rounds)} rounds, {len(subs)} songs, {len(votes)} votes, "
          f"{len(pname)} players; {enriched} songs with a year -> {OUT}")


if __name__ == "__main__":
    main()
