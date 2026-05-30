# spotify_lastfm_recs

Local tools for collecting Spotify playlist metadata and normalizing Spotify + Last.fm listening data into SQLite for recommendation experiments.

## What is committed

- `pull_spotify_playlists.py` fetches Spotify playlists, playlist detail, and playlist item metadata.
- `normalize_music_data.py` builds `music.db` from local Spotify export files, Last.fm JSON exports, and Spotify playlist pulls.
- `.gitignore` keeps credentials, tokens, exports, and generated databases out of git.

## Local inputs

Expected private local folders:

- `spotify_extended_streaming_history/`
- `spotify_playlists_export/`
- `lastfm/`

Expected private local config:

- `.env`
- `.spotify_token.json`

Run:

```bash
python3 pull_spotify_playlists.py
python3 normalize_music_data.py
```

The normalized database is written to `music.db`.
