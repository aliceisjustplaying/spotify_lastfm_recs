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
python3 reconcile_tracks.py
python3 qa_reconciliation.py
python3 apply_playlist_weights.py
python3 build_taste_features.py
python3 generate_recommendations.py
```

The normalized database is written to `music.db`.
Reconciliation and QA add canonical track tables to `music.db` and local CSV/Markdown reports under `reports/`.
Playlist weighting writes `playlist_weights` to SQLite and creates an ignored `playlist_weight_overrides.csv` for manual edits.
Taste features write `taste_features` to SQLite plus first-pass profile and seed reports under `reports/`.
Era boundaries are read from private local `taste_eras.json`, which is ignored by git.
Recommendations are written to ignored local CSV/Markdown files under `reports/`.
