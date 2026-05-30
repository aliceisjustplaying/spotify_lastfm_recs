#!/usr/bin/env python3
import csv
import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "music.db"
REPORT_DIR = ROOT / "reports"


GENERATED_NAME_RE = re.compile(
    r"\b(discover weekly|release radar|daily mix|radio|wrapped|top songs|spotify\.me|the sound of|the pulse of|needle|"
    r"this is |official|hits|best of|complete collection|soundtrack|original motion picture soundtrack|deluxe|"
    r"anniversary|remaster|now that'?s what i call music|topp 20)\b",
    re.I,
)
ALBUMISH_RE = re.compile(r"\s[–-]\s|original motion picture soundtrack|deluxe|remaster|anniversary|complete edition", re.I)


def write_csv(name, rows):
    path = REPORT_DIR / name
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path, 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path, len(rows)


def query(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params)]


def classify_playlist(row):
    name = row["name"] or ""
    owner = row["owner_id"] or ""
    tracks_total = row["tracks_total"] or 0
    followers = row["followers_total"] or 0
    reasons = []
    if owner == "spotify":
        reasons.append("spotify_owned")
    if GENERATED_NAME_RE.search(name):
        reasons.append("generated_or_editorial_name")
    if ALBUMISH_RE.search(name):
        reasons.append("album_or_soundtrack_name")
    if followers >= 1000 and owner != "ktamas":
        reasons.append("high_follower_external")
    if tracks_total <= 15 and ALBUMISH_RE.search(name):
        reasons.append("short_albumish_playlist")
    if not reasons and owner == "ktamas":
        bucket = "personal"
    elif "spotify_owned" in reasons or "generated_or_editorial_name" in reasons or "high_follower_external" in reasons:
        bucket = "weak_context"
    elif "album_or_soundtrack_name" in reasons or "short_albumish_playlist" in reasons:
        bucket = "album_or_collection"
    else:
        bucket = "unknown_external"
    return bucket, ";".join(reasons)


def write_playlist_classification(conn):
    rows = query(
        conn,
        """
        SELECT
            p.playlist_id,
            p.name,
            p.owner_id,
            p.owner_display_name,
            p.public,
            p.collaborative,
            p.followers_total,
            p.tracks_total,
            COALESCE(SUM(CASE WHEN ct.listen_count > 0 THEN 1 ELSE 0 END), 0) AS items_with_listens,
            COUNT(pi.id) AS item_rows
        FROM spotify_playlists p
        LEFT JOIN spotify_playlist_items pi ON pi.playlist_id = p.playlist_id
        LEFT JOIN canonical_tracks ct ON ct.spotify_track_id = pi.track_id
        GROUP BY p.playlist_id
        ORDER BY item_rows DESC, p.name
        """,
    )
    out = []
    for row in rows:
        bucket, reasons = classify_playlist(row)
        item_rows = row["item_rows"] or 0
        listened_ratio = (row["items_with_listens"] / item_rows) if item_rows else 0
        out.append(
            {
                **row,
                "bucket": bucket,
                "reasons": reasons,
                "listened_ratio": round(listened_ratio, 4),
            }
        )
    return write_csv("playlist_classification.csv", out)


def write_summary(conn, report_counts):
    counts = dict(
        conn.execute(
            """
            SELECT 'canonical_tracks', COUNT(*) FROM canonical_tracks
            UNION ALL SELECT 'canonical_events', COUNT(*) FROM canonical_track_events
            UNION ALL SELECT 'spotify_stream_events', COUNT(*) FROM canonical_track_events WHERE event_type='spotify_stream'
            UNION ALL SELECT 'lastfm_scrobble_events', COUNT(*) FROM canonical_track_events WHERE event_type='lastfm_scrobble'
            UNION ALL SELECT 'lastfm_loved_events', COUNT(*) FROM canonical_track_events WHERE event_type='lastfm_loved'
            UNION ALL SELECT 'playlist_item_events', COUNT(*) FROM canonical_track_events WHERE event_type='playlist_item'
            UNION ALL SELECT 'tracks_with_spotify_and_lastfm_listens', COUNT(*) FROM canonical_tracks WHERE spotify_listen_count > 0 AND lastfm_scrobble_count > 0
            UNION ALL SELECT 'tracks_with_spotify_id_and_lastfm_mbid', COUNT(*) FROM canonical_tracks WHERE spotify_track_id IS NOT NULL AND lastfm_track_mbid IS NOT NULL
            UNION ALL SELECT 'playlist_only_tracks', COUNT(*) FROM canonical_tracks WHERE listen_count = 0 AND playlist_item_count > 0
            UNION ALL SELECT 'loved_but_unplayed_tracks', COUNT(*) FROM canonical_tracks WHERE loved_count > 0 AND listen_count = 0
            """
        ).fetchall()
    )
    method_counts = query(
        conn,
        """
        SELECT match_method, COUNT(*) AS events
        FROM canonical_track_events
        GROUP BY match_method
        ORDER BY events DESC
        """,
    )
    confidence_counts = query(
        conn,
        """
        SELECT confidence_note, COUNT(*) AS tracks, SUM(listen_count) AS listens, SUM(playlist_item_count) AS playlist_items
        FROM canonical_tracks
        GROUP BY confidence_note
        ORDER BY tracks DESC
        """,
    )
    lines = ["# Reconciliation QA", ""]
    lines.append("## Counts")
    for key, value in counts.items():
        lines.append(f"- `{key}`: {value}")
    lines.append("")
    lines.append("## Match Methods")
    for row in method_counts:
        lines.append(f"- `{row['match_method']}`: {row['events']}")
    lines.append("")
    lines.append("## Canonical Confidence Notes")
    for row in confidence_counts:
        lines.append(
            f"- `{row['confidence_note']}`: {row['tracks']} tracks, {row['listens']} listens, {row['playlist_items']} playlist items"
        )
    lines.append("")
    lines.append("## Report Files")
    for name, count in report_counts:
        lines.append(f"- `{name}`: {count} rows")
    path = REPORT_DIR / "qa_summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main():
    REPORT_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        reports = []
        reports.append(
            write_csv(
                "suspicious_artist_title_splits.csv",
                query(
                    conn,
                    """
                    SELECT
                        norm_artist,
                        norm_track,
                        COUNT(*) AS canonical_count,
                        SUM(listen_count) AS listens,
                        SUM(playlist_item_count) AS playlist_items,
                        GROUP_CONCAT(canonical_track_id) AS canonical_ids,
                        GROUP_CONCAT(DISTINCT primary_artist_name || ' - ' || primary_track_name || ' [' || COALESCE(primary_album_name, '') || ']') AS variants
                    FROM canonical_tracks
                    WHERE norm_artist IS NOT NULL AND norm_track IS NOT NULL
                    GROUP BY norm_artist, norm_track
                    HAVING canonical_count > 1
                    ORDER BY listens DESC, canonical_count DESC
                    LIMIT 500
                    """,
                ),
            )
        )
        reports.append(
            write_csv(
                "low_confidence_high_listen_tracks.csv",
                query(
                    conn,
                    """
                    SELECT *
                    FROM canonical_track_summary
                    WHERE confidence_note NOT LIKE '%spotify_track_id%'
                      AND confidence_note NOT LIKE '%lastfm_track_mbid%'
                      AND listen_count >= 25
                    ORDER BY listen_count DESC
                    LIMIT 500
                    """,
                ),
            )
        )
        reports.append(
            write_csv(
                "multi_variant_canonical_tracks.csv",
                query(
                    conn,
                    """
                    SELECT
                        canonical_track_id,
                        COUNT(DISTINCT artist_name) AS artist_variants,
                        COUNT(DISTINCT track_name) AS title_variants,
                        COUNT(DISTINCT album_name) AS album_variants,
                        COUNT(DISTINCT spotify_track_id) AS spotify_ids,
                        COUNT(DISTINCT lastfm_track_mbid) AS lastfm_mbids,
                        COUNT(*) AS events,
                        GROUP_CONCAT(DISTINCT artist_name || ' - ' || track_name) AS variants
                    FROM canonical_track_events
                    GROUP BY canonical_track_id
                    HAVING artist_variants > 2 OR title_variants > 2 OR spotify_ids > 1 OR lastfm_mbids > 1
                    ORDER BY events DESC
                    LIMIT 500
                    """,
                ),
            )
        )
        reports.append(
            write_csv(
                "loved_but_low_play.csv",
                query(
                    conn,
                    """
                    SELECT *
                    FROM canonical_track_summary
                    WHERE loved_count > 0 AND listen_count <= 3
                    ORDER BY listen_count ASC, playlist_item_count DESC, primary_artist_name, primary_track_name
                    LIMIT 500
                    """,
                ),
            )
        )
        reports.append(
            write_csv(
                "playlist_only_tracks.csv",
                query(
                    conn,
                    """
                    SELECT *
                    FROM canonical_track_summary
                    WHERE listen_count = 0 AND playlist_item_count > 0
                    ORDER BY playlist_item_count DESC, primary_artist_name, primary_track_name
                    LIMIT 1000
                    """,
                ),
            )
        )
        reports.append(
            write_csv(
                "missing_metadata_events.csv",
                query(
                    conn,
                    """
                    SELECT *
                    FROM canonical_track_events
                    WHERE artist_name IS NULL OR track_name IS NULL OR trim(artist_name) = '' OR trim(track_name) = ''
                    ORDER BY source, source_table, source_id
                    LIMIT 1000
                    """,
                ),
            )
        )
        reports.append(write_playlist_classification(conn))
        summary_path = write_summary(conn, [(path.name, count) for path, count in reports])
        print(summary_path)
        for path, count in reports:
            print(f"{path}: {count}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
