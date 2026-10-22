# Music League UFCKed

Stats from the ufck Music Leagues: champions, best and worst songs, player and song lookup, voting habits, and who votes for whom. Filter by turbo or regular leagues.

Live site: https://bmickelson.github.io/ufck-musicleague-analysis/

## Rebuilding

1. Put each league's Music League export zip in `data/` (the zip's file name is the league id).
2. `python3 enrich.py` looks up release years, lengths, cover art (Spotify) and genres (MusicBrainz). It caches results in `cache/`, so reruns only fetch new songs.
3. `python3 build.py` writes `docs/index.html`.

Player photos load from Music League by player id; put a {player id: url} override in `avatars.json` if one is wrong. League names and the turbo flag live in `leagues.json`. The page title and tagline live in `site.json`.

The raw exports and lookup cache are not committed.
