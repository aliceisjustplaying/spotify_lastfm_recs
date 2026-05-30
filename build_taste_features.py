#!/usr/bin/env python3
import csv
import math
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "music.db"
REPORT_DIR = ROOT / "reports"


def create_schema(conn):
    conn.executescript(
        """
        DROP TABLE IF EXISTS taste_features;

        CREATE TABLE taste_features (
            canonical_track_id INTEGER PRIMARY KEY,
            artist_name TEXT,
            track_name TEXT,
            album_name TEXT,
            spotify_track_id TEXT,
            spotify_track_uri TEXT,
            lastfm_track_mbid TEXT,
            listen_count INTEGER NOT NULL,
            spotify_listen_count INTEGER NOT NULL,
            lastfm_scrobble_count INTEGER NOT NULL,
            loved_count INTEGER NOT NULL,
            playlist_item_count INTEGER NOT NULL,
            weighted_playlist_score REAL NOT NULL,
            playlist_bucket_count INTEGER NOT NULL,
            skip_count INTEGER NOT NULL,
            skip_rate REAL,
            first_seen_at TEXT,
            last_seen_at TEXT,
            first_seen_year INTEGER,
            last_seen_year INTEGER,
            active_year_count INTEGER NOT NULL,
            recent_listen_count INTEGER NOT NULL,
            old_listen_count INTEGER NOT NULL,
            listen_score REAL NOT NULL,
            love_score REAL NOT NULL,
            playlist_score REAL NOT NULL,
            recency_score REAL NOT NULL,
            longevity_score REAL NOT NULL,
            skip_penalty REAL NOT NULL,
            total_score REAL NOT NULL,
            seed_reason TEXT
        );

        CREATE INDEX idx_taste_features_total ON taste_features(total_score DESC);
        CREATE INDEX idx_taste_features_artist ON taste_features(artist_name);
        """
    )


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def score_row(row):
    listen_score = math.log1p(row["listen_count"]) * 12
    love_score = row["loved_count"] * 30
    playlist_score = math.log1p(row["weighted_playlist_score"]) * 10
    recency_score = math.log1p(row["recent_listen_count"]) * 8
    longevity_score = min(row["active_year_count"], 10) * 2.5
    skip_rate = row["skip_rate"]
    skip_penalty = (skip_rate * 18) if skip_rate is not None and row["spotify_listen_count"] >= 5 else 0
    total = listen_score + love_score + playlist_score + recency_score + longevity_score - skip_penalty

    reasons = []
    if row["listen_count"] >= 100:
        reasons.append("heavy_rotation")
    if row["loved_count"]:
        reasons.append("loved")
    if row["weighted_playlist_score"] >= 3:
        reasons.append("playlist_anchor")
    if row["recent_listen_count"] >= 5:
        reasons.append("recent")
    if row["active_year_count"] >= 5:
        reasons.append("long_running")
    if not reasons:
        reasons.append("candidate")
    return listen_score, love_score, playlist_score, recency_score, longevity_score, skip_penalty, total, ",".join(reasons)


def build_features(conn):
    rows = []
    for row in conn.execute(
        """
        WITH playlist AS (
            SELECT
                canonical_track_id,
                SUM(playlist_weight) AS weighted_playlist_score,
                COUNT(DISTINCT playlist_bucket) AS playlist_bucket_count
            FROM weighted_playlist_track_signals
            GROUP BY canonical_track_id
        ),
        skips AS (
            SELECT
                cte.canonical_track_id,
                SUM(CASE WHEN ss.skipped THEN 1 ELSE 0 END) AS skip_count,
                COUNT(*) AS spotify_events
            FROM canonical_track_events cte
            JOIN spotify_streams ss ON ss.id = cte.source_id
            WHERE cte.source_table = 'spotify_streams'
            GROUP BY cte.canonical_track_id
        ),
        years AS (
            SELECT
                canonical_track_id,
                COUNT(DISTINCT strftime('%Y', event_at)) AS active_year_count,
                MIN(CAST(strftime('%Y', event_at) AS INTEGER)) AS first_seen_year,
                MAX(CAST(strftime('%Y', event_at) AS INTEGER)) AS last_seen_year
            FROM canonical_track_events
            WHERE event_at IS NOT NULL AND event_type IN ('spotify_stream', 'lastfm_scrobble')
            GROUP BY canonical_track_id
        ),
        recent AS (
            SELECT
                canonical_track_id,
                SUM(CASE WHEN event_at >= '2024-01-01T00:00:00Z' THEN 1 ELSE 0 END) AS recent_listen_count,
                SUM(CASE WHEN event_at < '2020-01-01T00:00:00Z' THEN 1 ELSE 0 END) AS old_listen_count
            FROM canonical_track_events
            WHERE event_type IN ('spotify_stream', 'lastfm_scrobble')
            GROUP BY canonical_track_id
        )
        SELECT
            ct.canonical_track_id,
            ct.primary_artist_name AS artist_name,
            ct.primary_track_name AS track_name,
            ct.primary_album_name AS album_name,
            ct.spotify_track_id,
            ct.spotify_track_uri,
            ct.lastfm_track_mbid,
            ct.listen_count,
            ct.spotify_listen_count,
            ct.lastfm_scrobble_count,
            ct.loved_count,
            ct.playlist_item_count,
            COALESCE(playlist.weighted_playlist_score, 0) AS weighted_playlist_score,
            COALESCE(playlist.playlist_bucket_count, 0) AS playlist_bucket_count,
            COALESCE(skips.skip_count, 0) AS skip_count,
            CASE WHEN skips.spotify_events > 0 THEN CAST(skips.skip_count AS REAL) / skips.spotify_events ELSE NULL END AS skip_rate,
            ct.first_seen_at,
            ct.last_seen_at,
            years.first_seen_year,
            years.last_seen_year,
            COALESCE(years.active_year_count, 0) AS active_year_count,
            COALESCE(recent.recent_listen_count, 0) AS recent_listen_count,
            COALESCE(recent.old_listen_count, 0) AS old_listen_count
        FROM canonical_tracks ct
        LEFT JOIN playlist ON playlist.canonical_track_id = ct.canonical_track_id
        LEFT JOIN skips ON skips.canonical_track_id = ct.canonical_track_id
        LEFT JOIN years ON years.canonical_track_id = ct.canonical_track_id
        LEFT JOIN recent ON recent.canonical_track_id = ct.canonical_track_id
        WHERE ct.primary_artist_name IS NOT NULL AND ct.primary_track_name IS NOT NULL
        """
    ):
        row = dict(row)
        scores = score_row(row)
        rows.append(
            (
                row["canonical_track_id"],
                row["artist_name"],
                row["track_name"],
                row["album_name"],
                row["spotify_track_id"],
                row["spotify_track_uri"],
                row["lastfm_track_mbid"],
                row["listen_count"],
                row["spotify_listen_count"],
                row["lastfm_scrobble_count"],
                row["loved_count"],
                row["playlist_item_count"],
                row["weighted_playlist_score"],
                row["playlist_bucket_count"],
                row["skip_count"],
                row["skip_rate"],
                row["first_seen_at"],
                row["last_seen_at"],
                row["first_seen_year"],
                row["last_seen_year"],
                row["active_year_count"],
                row["recent_listen_count"],
                row["old_listen_count"],
                *scores,
            )
        )
    conn.executemany(
        """
        INSERT INTO taste_features(
            canonical_track_id, artist_name, track_name, album_name, spotify_track_id, spotify_track_uri,
            lastfm_track_mbid, listen_count, spotify_listen_count, lastfm_scrobble_count, loved_count,
            playlist_item_count, weighted_playlist_score, playlist_bucket_count, skip_count, skip_rate,
            first_seen_at, last_seen_at, first_seen_year, last_seen_year, active_year_count,
            recent_listen_count, old_listen_count, listen_score, love_score, playlist_score,
            recency_score, longevity_score, skip_penalty, total_score, seed_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params)]


def write_csv(path, data):
    data = list(data)
    if not data:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)


def markdown_table(data, columns):
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in data:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join(lines)


def write_reports(conn):
    REPORT_DIR.mkdir(exist_ok=True)
    seeds = rows(
        conn,
        """
        SELECT
            canonical_track_id,
            artist_name,
            track_name,
            album_name,
            spotify_track_id,
            listen_count,
            loved_count,
            ROUND(weighted_playlist_score, 2) AS weighted_playlist_score,
            recent_listen_count,
            active_year_count,
            ROUND(total_score, 2) AS total_score,
            seed_reason
        FROM taste_features
        WHERE listen_count >= 5 OR loved_count > 0 OR weighted_playlist_score >= 1
        ORDER BY total_score DESC
        LIMIT 500
        """,
    )
    write_csv(REPORT_DIR / "recommendation_seeds.csv", seeds)

    forgotten = rows(
        conn,
        """
        SELECT artist_name, track_name, album_name, listen_count, loved_count, last_seen_year, ROUND(total_score, 2) AS total_score
        FROM taste_features
        WHERE (loved_count > 0 OR listen_count >= 40)
          AND recent_listen_count = 0
          AND last_seen_year <= 2021
        ORDER BY total_score DESC
        LIMIT 100
        """,
    )
    write_csv(REPORT_DIR / "forgotten_favorites.csv", forgotten)

    recent = rows(
        conn,
        """
        SELECT artist_name, track_name, album_name, listen_count, recent_listen_count, loved_count, ROUND(total_score, 2) AS total_score
        FROM taste_features
        WHERE recent_listen_count > 0
        ORDER BY recent_listen_count DESC, total_score DESC
        LIMIT 100
        """,
    )
    write_csv(REPORT_DIR / "recent_obsessions.csv", recent)

    playlist_only = rows(
        conn,
        """
        SELECT artist_name, track_name, album_name, playlist_item_count, ROUND(weighted_playlist_score, 2) AS weighted_playlist_score
        FROM taste_features
        WHERE listen_count = 0 AND weighted_playlist_score >= 1
        ORDER BY weighted_playlist_score DESC
        LIMIT 100
        """,
    )
    write_csv(REPORT_DIR / "playlist_only_candidates.csv", playlist_only)

    top_artists = rows(
        conn,
        """
        SELECT artist_name, COUNT(*) AS tracks, SUM(listen_count) AS listens, SUM(loved_count) AS loved,
               ROUND(SUM(total_score), 2) AS artist_score
        FROM taste_features
        GROUP BY artist_name
        ORDER BY artist_score DESC
        LIMIT 30
        """,
    )

    top_tracks = seeds[:25]
    lines = ["# Taste Profile", ""]
    lines.append("## Top Taste Anchors")
    lines.append(markdown_table(top_tracks[:20], ["artist_name", "track_name", "listen_count", "loved_count", "weighted_playlist_score", "total_score", "seed_reason"]))
    lines.append("")
    lines.append("## Top Artists")
    lines.append(markdown_table(top_artists[:20], ["artist_name", "tracks", "listens", "loved", "artist_score"]))
    lines.append("")
    lines.append("## Forgotten Favorites")
    lines.append(markdown_table(forgotten[:20], ["artist_name", "track_name", "listen_count", "loved_count", "last_seen_year", "total_score"]))
    lines.append("")
    lines.append("## Recent Obsessions")
    lines.append(markdown_table(recent[:20], ["artist_name", "track_name", "listen_count", "recent_listen_count", "loved_count", "total_score"]))
    lines.append("")
    lines.append("## Generated Files")
    lines.append("- `reports/recommendation_seeds.csv`")
    lines.append("- `reports/forgotten_favorites.csv`")
    lines.append("- `reports/recent_obsessions.csv`")
    lines.append("- `reports/playlist_only_candidates.csv`")
    (REPORT_DIR / "taste_profile.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            create_schema(conn)
            count = build_features(conn)
            conn.execute("ANALYZE")
        write_reports(conn)
        print(f"taste_features: {count}")
        print(REPORT_DIR / "taste_profile.md")
        print(REPORT_DIR / "recommendation_seeds.csv")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
