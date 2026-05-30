#!/usr/bin/env python3
import csv
import sqlite3
from pathlib import Path

from playlist_rules import classify_playlist


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "music.db"
REPORT_DIR = ROOT / "reports"
OVERRIDES_PATH = ROOT / "playlist_weight_overrides.csv"


def load_overrides():
    if not OVERRIDES_PATH.exists():
        return {}
    overrides = {}
    with OVERRIDES_PATH.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            playlist_id = (row.get("playlist_id") or "").strip()
            manual = (row.get("manual_weight") or "").strip()
            if not playlist_id or not manual:
                continue
            overrides[playlist_id] = {
                "manual_weight": float(manual),
                "manual_bucket": (row.get("manual_bucket") or "").strip(),
                "notes": (row.get("notes") or "").strip(),
            }
    return overrides


def write_overrides_template(rows):
    if OVERRIDES_PATH.exists():
        return
    sorted_rows = sorted(rows, key=lambda r: (r["review_priority"], r["item_rows"]), reverse=True)
    fieldnames = [
        "playlist_id",
        "name",
        "owner_id",
        "owner_display_name",
        "tracks_total",
        "item_rows",
        "listened_ratio",
        "auto_bucket",
        "auto_weight",
        "manual_weight",
        "manual_bucket",
        "notes",
    ]
    with OVERRIDES_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted_rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def playlist_rows(conn):
    rows = []
    for row in conn.execute(
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
            COALESCE(SUM(CASE WHEN ct.loved_count > 0 THEN 1 ELSE 0 END), 0) AS items_loved,
            COUNT(pi.id) AS item_rows
        FROM spotify_playlists p
        LEFT JOIN spotify_playlist_items pi ON pi.playlist_id = p.playlist_id
        LEFT JOIN canonical_tracks ct ON ct.spotify_track_id = pi.track_id
        GROUP BY p.playlist_id
        """
    ):
        row = dict(row)
        item_rows = row["item_rows"] or 0
        listened_ratio = (row["items_with_listens"] / item_rows) if item_rows else 0
        row["listened_ratio"] = round(listened_ratio, 4)
        bucket, weight, reasons = classify_playlist(row)
        row["auto_bucket"] = bucket
        row["auto_weight"] = weight
        row["reasons"] = reasons
        row["review_priority"] = round((row["items_with_listens"] * 3) + row["items_loved"] + (item_rows * weight / 20), 4)
        rows.append(row)
    return rows


def create_schema(conn):
    conn.executescript(
        """
        DROP TABLE IF EXISTS playlist_weights;
        DROP VIEW IF EXISTS weighted_playlist_track_signals;

        CREATE TABLE playlist_weights (
            playlist_id TEXT PRIMARY KEY,
            name TEXT,
            owner_id TEXT,
            owner_display_name TEXT,
            tracks_total INTEGER,
            item_rows INTEGER,
            items_with_listens INTEGER,
            items_loved INTEGER,
            listened_ratio REAL,
            auto_bucket TEXT,
            auto_weight REAL,
            manual_bucket TEXT,
            manual_weight REAL,
            effective_bucket TEXT,
            effective_weight REAL,
            reasons TEXT,
            notes TEXT,
            review_priority REAL
        );

        CREATE VIEW weighted_playlist_track_signals AS
        SELECT
            cte.canonical_track_id,
            cte.source_id AS playlist_item_event_id,
            cte.playlist_id,
            pw.effective_weight AS playlist_weight,
            pw.effective_bucket AS playlist_bucket,
            cte.event_at AS added_at,
            cte.artist_name,
            cte.track_name,
            cte.album_name
        FROM canonical_track_events cte
        JOIN playlist_weights pw ON pw.playlist_id = cte.playlist_id
        WHERE cte.event_type = 'playlist_item';
        """
    )


def write_review_report(rows):
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / "playlist_weight_review.csv"
    fieldnames = [
        "playlist_id",
        "name",
        "owner_id",
        "owner_display_name",
        "item_rows",
        "items_with_listens",
        "items_loved",
        "listened_ratio",
        "auto_bucket",
        "auto_weight",
        "manual_weight",
        "effective_weight",
        "review_priority",
        "reasons",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: r["review_priority"], reverse=True):
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    return path


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = playlist_rows(conn)
        write_overrides_template(rows)
        overrides = load_overrides()
        for row in rows:
            override = overrides.get(row["playlist_id"], {})
            manual_weight = override.get("manual_weight")
            row["manual_weight"] = manual_weight
            row["manual_bucket"] = override.get("manual_bucket")
            row["notes"] = override.get("notes")
            row["effective_weight"] = manual_weight if manual_weight is not None else row["auto_weight"]
            row["effective_bucket"] = row["manual_bucket"] or row["auto_bucket"]

        with conn:
            create_schema(conn)
            conn.executemany(
                """
                INSERT INTO playlist_weights(
                    playlist_id, name, owner_id, owner_display_name, tracks_total, item_rows,
                    items_with_listens, items_loved, listened_ratio, auto_bucket, auto_weight,
                    manual_bucket, manual_weight, effective_bucket, effective_weight, reasons,
                    notes, review_priority
                ) VALUES (
                    :playlist_id, :name, :owner_id, :owner_display_name, :tracks_total, :item_rows,
                    :items_with_listens, :items_loved, :listened_ratio, :auto_bucket, :auto_weight,
                    :manual_bucket, :manual_weight, :effective_bucket, :effective_weight, :reasons,
                    :notes, :review_priority
                )
                """,
                rows,
            )
            conn.execute("ANALYZE")
        report = write_review_report(rows)
        print(f"playlist_weights: {len(rows)}")
        print(f"overrides: {len(overrides)}")
        print(f"template: {OVERRIDES_PATH}")
        print(f"review: {report}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
