#!/usr/bin/env python3
import csv
import json
import math
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from pull_spotify_playlists import API_URL, get_access_token, load_dotenv, require_env
from reconcile_tracks import simple_key


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "music.db"
REPORT_DIR = ROOT / "reports"


def spotify_get(path, token, params=None):
    if params:
        path = f"{path}?{urllib.parse.urlencode(params, doseq=True)}"
    url = path if path.startswith("https://") else f"{API_URL}{path}"
    for _ in range(4):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(int(exc.headers.get("Retry-After", "2")))
                continue
            if exc.code in (400, 403, 404):
                return None
            raise
    return None


def rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params)]


def heard_sets(conn):
    track_ids = {
        row["spotify_track_id"]
        for row in conn.execute("SELECT spotify_track_id FROM taste_features WHERE spotify_track_id IS NOT NULL")
    }
    keys = {
        simple_key(row["artist_name"], row["track_name"])
        for row in conn.execute("SELECT artist_name, track_name FROM taste_features")
    }
    return track_ids, {key for key in keys if key}


def seed_rows(conn, score_col, where, limit):
    return rows(
        conn,
        f"""
        SELECT
            tf.canonical_track_id,
            tf.artist_name,
            tf.track_name,
            tf.spotify_track_id,
            tf.listen_count,
            tf.loved_count,
            tf.dominant_era,
            ROUND(tf.{score_col}, 2) AS seed_score,
            GROUP_CONCAT(DISTINCT sta.artist_id) AS artist_ids
        FROM taste_features tf
        LEFT JOIN spotify_track_artists sta ON sta.track_id = tf.spotify_track_id
        WHERE {where}
        GROUP BY tf.canonical_track_id
        ORDER BY tf.{score_col} DESC
        LIMIT ?
        """,
        (limit,),
    )


def track_summary(track):
    artists = track.get("artists") or []
    album = track.get("album") or {}
    return {
        "track_id": track.get("id"),
        "track_name": track.get("name"),
        "artist_name": ", ".join(a.get("name") for a in artists if a.get("name")),
        "artist_ids": ",".join(a.get("id") for a in artists if a.get("id")),
        "album_name": album.get("name"),
        "release_date": album.get("release_date"),
        "popularity": track.get("popularity"),
        "spotify_url": ((track.get("external_urls") or {}).get("spotify")),
    }


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def full_tracks(token, track_ids):
    tracks = []
    for batch in chunks(list(dict.fromkeys(track_ids)), 50):
        data = spotify_get("/tracks", token, {"ids": ",".join(batch), "market": "US"}) or {}
        tracks.extend(track for track in data.get("tracks") or [] if track)
    return tracks


def add_candidate(candidates, track, source, seed, base_score, heard_ids, heard_keys):
    if not track or not track.get("id") or track.get("id") in heard_ids:
        return
    artists = track.get("artists") or []
    artist_name = ", ".join(a.get("name") for a in artists if a.get("name"))
    if simple_key(artist_name, track.get("name")) in heard_keys:
        return
    item = track_summary(track)
    if not item["track_name"] or not item["artist_name"]:
        return
    existing = candidates.get(item["track_id"])
    popularity = item["popularity"] or 0
    score = base_score + math.log1p(popularity) * 2
    reason = f"{source} from {seed['artist_name']} - {seed['track_name']}"
    if existing:
        existing["candidate_score"] += score * 0.4
        existing["why"] += f"; {reason}"
        return
    item.update(
        {
            "candidate_score": round(score, 3),
            "seed_artist": seed["artist_name"],
            "seed_track": seed["track_name"],
            "seed_score": seed["seed_score"],
            "seed_era": seed.get("dominant_era"),
            "source": source,
            "why": reason,
        }
    )
    candidates[item["track_id"]] = item


def pull_artist_candidates(token, seed, candidates, heard_ids, heard_keys, base_multiplier):
    artist_ids = [x for x in (seed.get("artist_ids") or "").split(",") if x]
    for artist_id in artist_ids[:2]:
        top = spotify_get(f"/artists/{artist_id}/top-tracks", token, {"market": "US"}) or {}
        for track in top.get("tracks") or []:
            add_candidate(candidates, track, "artist_top_track", seed, seed["seed_score"] * base_multiplier, heard_ids, heard_keys)

        albums = spotify_get(
            f"/artists/{artist_id}/albums",
            token,
            {"include_groups": "album,single", "market": "US", "limit": 5},
        ) or {}
        album_track_ids = []
        for album in albums.get("items") or []:
            tracks = spotify_get(f"/albums/{album['id']}/tracks", token, {"market": "US", "limit": 20}) or {}
            for album_track in tracks.get("items") or []:
                if album_track.get("id"):
                    album_track_ids.append(album_track["id"])
        for full in full_tracks(token, album_track_ids[:80]):
            add_candidate(candidates, full, "artist_catalog", seed, seed["seed_score"] * base_multiplier * 0.72, heard_ids, heard_keys)


def local_playlist_candidates(conn, profile, candidates, heard_ids, heard_keys):
    if profile == "current":
        order = "tf.current_taste_score"
        where = "tf.listen_count = 0 AND tf.weighted_playlist_score >= 1"
    elif profile == "rediscovery":
        order = "tf.rediscovery_score"
        where = "tf.listen_count = 0 AND tf.weighted_playlist_score >= 0.7"
    else:
        order = "tf.recommendation_seed_score"
        where = "tf.listen_count = 0 AND tf.weighted_playlist_score >= 1"
    for row in rows(
        conn,
        f"""
        SELECT
            tf.spotify_track_id AS track_id,
            tf.track_name,
            tf.artist_name,
            tf.album_name,
            tf.weighted_playlist_score,
            tf.dominant_era,
            st.popularity,
            st.spotify_url
        FROM taste_features tf
        LEFT JOIN spotify_tracks st ON st.track_id = tf.spotify_track_id
        WHERE {where}
        ORDER BY {order} DESC, tf.weighted_playlist_score DESC
        LIMIT 200
        """,
    ):
        if not row["track_id"] or row["track_id"] in heard_ids:
            continue
        if simple_key(row["artist_name"], row["track_name"]) in heard_keys:
            continue
        candidates.setdefault(
            row["track_id"],
            {
                "track_id": row["track_id"],
                "track_name": row["track_name"],
                "artist_name": row["artist_name"],
                "artist_ids": "",
                "album_name": row["album_name"],
                "release_date": "",
                "popularity": row["popularity"],
                "spotify_url": row["spotify_url"],
                "candidate_score": round((row["weighted_playlist_score"] or 0) * 18, 3),
                "seed_artist": "",
                "seed_track": "",
                "seed_score": "",
                "seed_era": row["dominant_era"],
                "source": "playlist_only",
                "why": "unheard track from weighted playlists",
            },
        )


def write_csv(path, data):
    data = sorted(data, key=lambda row: row["candidate_score"], reverse=True)
    if not data:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)


def diversify(items, limit=150, per_artist=4, per_seed=8):
    result = []
    artist_counts = defaultdict(int)
    seed_counts = defaultdict(int)
    for item in sorted(items, key=lambda row: row["candidate_score"], reverse=True):
        primary_artist = (item["artist_name"] or "").split(",", 1)[0].strip()
        seed_key = f"{item.get('seed_artist')}|{item.get('seed_track')}"
        if artist_counts[primary_artist] >= per_artist:
            continue
        if seed_counts[seed_key] >= per_seed:
            continue
        item["candidate_score"] = round(item["candidate_score"], 3)
        result.append(item)
        artist_counts[primary_artist] += 1
        seed_counts[seed_key] += 1
        if len(result) >= limit:
            break
    return result


def generate_profile(conn, token, name, seeds, heard_ids, heard_keys, multiplier):
    candidates = {}
    for seed in seeds:
        pull_artist_candidates(token, seed, candidates, heard_ids, heard_keys, multiplier)
    local_playlist_candidates(conn, name, candidates, heard_ids, heard_keys)
    result = diversify(candidates.values())
    write_csv(REPORT_DIR / f"recommendations_{name}.csv", result)
    return result


def write_summary(results):
    lines = ["# Recommendations", ""]
    for name, items in results.items():
        lines.append(f"## {name.title()}")
        lines.append("| artist | track | source | score | why |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in items[:20]:
            lines.append(
                f"| {item['artist_name']} | {item['track_name']} | {item['source']} | {round(item['candidate_score'], 2)} | {item['why']} |"
            )
        lines.append("")
    (REPORT_DIR / "recommendations.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    load_dotenv()
    token = get_access_token(
        require_env("SPOTIFY_CLIENT_ID"),
        require_env("SPOTIFY_CLIENT_SECRET"),
        require_env("SPOTIFY_REDIRECT_URI"),
    )
    REPORT_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        heard_ids, heard_keys = heard_sets(conn)
        profiles = {
            "current": seed_rows(conn, "current_taste_score", "tf.era_4_listens > 0 AND tf.spotify_track_id IS NOT NULL", 18),
            "rediscovery": seed_rows(conn, "rediscovery_score", "tf.era_4_listens = 0 AND tf.spotify_track_id IS NOT NULL", 18),
            "bridge": seed_rows(conn, "recommendation_seed_score", "tf.spotify_track_id IS NOT NULL", 24),
        }
        results = {
            name: generate_profile(conn, token, name, seeds, heard_ids, heard_keys, 0.42)
            for name, seeds in profiles.items()
        }
        write_summary(results)
        for name, items in results.items():
            print(f"{name}: {len(items)}")
        print(REPORT_DIR / "recommendations.md")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
