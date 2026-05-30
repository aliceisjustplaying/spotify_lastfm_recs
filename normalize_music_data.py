#!/usr/bin/env python3
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "music.db"


def jdump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    h = hashlib.sha256()
    if path.is_dir():
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            h.update(str(child.relative_to(path)).encode())
            h.update(b"\0")
            h.update(sha256(child).encode())
            h.update(b"\0")
        return h.hexdigest()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iso_from_uts(value):
    if not value:
        return None
    return dt.datetime.fromtimestamp(int(value), dt.UTC).isoformat().replace("+00:00", "Z")


def spotify_url(obj):
    return ((obj or {}).get("external_urls") or {}).get("spotify")


def spotify_id_from_uri(uri):
    if not uri or ":" not in uri:
        return None
    return uri.rsplit(":", 1)[-1]


def flatten_pages(data):
    if isinstance(data, list) and data and all(isinstance(page, list) for page in data):
        for page_index, page in enumerate(data):
            for item_index, item in enumerate(page):
                yield page_index, item_index, item
    elif isinstance(data, list):
        for item_index, item in enumerate(data):
            yield 0, item_index, item


def lastfm_artist(item):
    artist = item.get("artist") or {}
    return artist.get("#text") or artist.get("name"), artist.get("mbid")


def create_schema(conn):
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        DROP TABLE IF EXISTS source_files;
        DROP TABLE IF EXISTS spotify_streams;
        DROP TABLE IF EXISTS lastfm_scrobbles;
        DROP TABLE IF EXISTS lastfm_loved_tracks;
        DROP TABLE IF EXISTS spotify_playlist_entries;
        DROP TABLE IF EXISTS spotify_playlists;
        DROP TABLE IF EXISTS spotify_playlist_items;
        DROP TABLE IF EXISTS spotify_tracks;
        DROP TABLE IF EXISTS spotify_artists;
        DROP TABLE IF EXISTS spotify_albums;
        DROP TABLE IF EXISTS spotify_track_artists;
        DROP TABLE IF EXISTS spotify_album_artists;
        DROP VIEW IF EXISTS all_listens;
        DROP VIEW IF EXISTS track_signals;

        CREATE TABLE source_files (
            path TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL
        );

        CREATE TABLE spotify_streams (
            id INTEGER PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_index INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            played_at TEXT,
            ms_played INTEGER,
            platform TEXT,
            conn_country TEXT,
            ip_addr TEXT,
            track_name TEXT,
            artist_name TEXT,
            album_name TEXT,
            spotify_track_uri TEXT,
            spotify_track_id TEXT,
            episode_name TEXT,
            episode_show_name TEXT,
            spotify_episode_uri TEXT,
            audiobook_title TEXT,
            audiobook_uri TEXT,
            audiobook_chapter_uri TEXT,
            audiobook_chapter_title TEXT,
            reason_start TEXT,
            reason_end TEXT,
            shuffle INTEGER,
            skipped INTEGER,
            offline INTEGER,
            offline_timestamp INTEGER,
            incognito_mode INTEGER,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE lastfm_scrobbles (
            id INTEGER PRIMARY KEY,
            source_file TEXT NOT NULL,
            page_index INTEGER NOT NULL,
            item_index INTEGER NOT NULL,
            played_at TEXT,
            played_at_text TEXT,
            artist_name TEXT,
            artist_mbid TEXT,
            album_name TEXT,
            album_mbid TEXT,
            track_name TEXT,
            track_mbid TEXT,
            url TEXT,
            streamable TEXT,
            image_json TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE lastfm_loved_tracks (
            id INTEGER PRIMARY KEY,
            source_file TEXT NOT NULL,
            page_index INTEGER NOT NULL,
            item_index INTEGER NOT NULL,
            loved_at TEXT,
            loved_at_text TEXT,
            artist_name TEXT,
            artist_mbid TEXT,
            track_name TEXT,
            track_mbid TEXT,
            url TEXT,
            streamable TEXT,
            image_json TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE spotify_playlist_entries (
            entry_index INTEGER PRIMARY KEY,
            playlist_id TEXT NOT NULL,
            name TEXT,
            owner_id TEXT,
            owner_display_name TEXT,
            public INTEGER,
            collaborative INTEGER,
            snapshot_id TEXT,
            tracks_total INTEGER,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE spotify_playlists (
            playlist_id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            owner_id TEXT,
            owner_display_name TEXT,
            owner_uri TEXT,
            owner_url TEXT,
            public INTEGER,
            collaborative INTEGER,
            followers_total INTEGER,
            snapshot_id TEXT,
            tracks_total INTEGER,
            href TEXT,
            spotify_url TEXT,
            images_json TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE spotify_playlist_items (
            id INTEGER PRIMARY KEY,
            playlist_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            added_at TEXT,
            added_by_id TEXT,
            added_by_display_name TEXT,
            added_by_uri TEXT,
            added_by_url TEXT,
            is_local INTEGER,
            primary_color TEXT,
            track_id TEXT,
            track_uri TEXT,
            track_href TEXT,
            track_type TEXT,
            track_name TEXT,
            artists_text TEXT,
            artists_json TEXT,
            album_id TEXT,
            album_name TEXT,
            album_type TEXT,
            album_release_date TEXT,
            album_release_date_precision TEXT,
            album_artists_text TEXT,
            album_artists_json TEXT,
            duration_ms INTEGER,
            explicit INTEGER,
            popularity INTEGER,
            preview_url TEXT,
            spotify_url TEXT,
            disc_number INTEGER,
            track_number INTEGER,
            is_playable INTEGER,
            raw_track_json TEXT,
            raw_item_json TEXT NOT NULL
        );

        CREATE TABLE spotify_tracks (
            track_id TEXT PRIMARY KEY,
            track_uri TEXT,
            href TEXT,
            name TEXT,
            artists_text TEXT,
            artists_json TEXT,
            album_id TEXT,
            album_name TEXT,
            duration_ms INTEGER,
            explicit INTEGER,
            popularity INTEGER,
            preview_url TEXT,
            spotify_url TEXT,
            external_ids_json TEXT,
            available_markets_json TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE spotify_artists (
            artist_id TEXT PRIMARY KEY,
            name TEXT,
            uri TEXT,
            href TEXT,
            spotify_url TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE spotify_albums (
            album_id TEXT PRIMARY KEY,
            name TEXT,
            album_type TEXT,
            release_date TEXT,
            release_date_precision TEXT,
            total_tracks INTEGER,
            uri TEXT,
            href TEXT,
            spotify_url TEXT,
            images_json TEXT,
            available_markets_json TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE spotify_track_artists (
            track_id TEXT NOT NULL,
            artist_id TEXT,
            position INTEGER NOT NULL,
            artist_name TEXT,
            PRIMARY KEY (track_id, position)
        );

        CREATE TABLE spotify_album_artists (
            album_id TEXT NOT NULL,
            artist_id TEXT,
            position INTEGER NOT NULL,
            artist_name TEXT,
            PRIMARY KEY (album_id, position)
        );

        CREATE INDEX idx_spotify_streams_played_at ON spotify_streams(played_at);
        CREATE INDEX idx_spotify_streams_track_uri ON spotify_streams(spotify_track_uri);
        CREATE INDEX idx_spotify_streams_artist_track ON spotify_streams(artist_name, track_name);
        CREATE INDEX idx_lastfm_scrobbles_played_at ON lastfm_scrobbles(played_at);
        CREATE INDEX idx_lastfm_scrobbles_artist_track ON lastfm_scrobbles(artist_name, track_name);
        CREATE INDEX idx_lastfm_loved_artist_track ON lastfm_loved_tracks(artist_name, track_name);
        CREATE INDEX idx_playlist_items_playlist ON spotify_playlist_items(playlist_id, position);
        CREATE INDEX idx_playlist_items_track ON spotify_playlist_items(track_id);
        CREATE INDEX idx_playlist_items_artist_track ON spotify_playlist_items(artists_text, track_name);

        CREATE VIEW all_listens AS
        SELECT
            'spotify' AS source,
            id AS source_id,
            played_at,
            artist_name,
            track_name,
            album_name,
            spotify_track_uri,
            spotify_track_id,
            ms_played,
            platform,
            conn_country,
            skipped,
            raw_json
        FROM spotify_streams
        WHERE track_name IS NOT NULL
        UNION ALL
        SELECT
            'lastfm' AS source,
            id AS source_id,
            played_at,
            artist_name,
            track_name,
            album_name,
            NULL AS spotify_track_uri,
            NULL AS spotify_track_id,
            NULL AS ms_played,
            NULL AS platform,
            NULL AS conn_country,
            NULL AS skipped,
            raw_json
        FROM lastfm_scrobbles;

        CREATE VIEW track_signals AS
        SELECT 'listen' AS signal, source, played_at AS signal_at, artist_name, track_name, album_name, spotify_track_id, raw_json
        FROM all_listens
        UNION ALL
        SELECT 'loved', 'lastfm', loved_at, artist_name, track_name, NULL, NULL, raw_json
        FROM lastfm_loved_tracks
        UNION ALL
        SELECT 'playlist_add', 'spotify', added_at, artists_text, track_name, album_name, track_id, raw_item_json
        FROM spotify_playlist_items
        WHERE track_name IS NOT NULL;
        """
    )


def record_source(conn, path, kind, row_count):
    conn.execute(
        "INSERT INTO source_files(path, kind, sha256, row_count, imported_at) VALUES (?, ?, ?, ?, ?)",
        (str(path.relative_to(ROOT)), kind, sha256(path), row_count, dt.datetime.now(dt.UTC).isoformat()),
    )


def insert_spotify_streams(conn):
    rows = []
    for path in sorted((ROOT / "spotify_extended_streaming_history").glob("Streaming_History_*.json")):
        data = read_json(path)
        media_type = "video" if "_Video_" in path.name else "audio"
        record_source(conn, path, f"spotify_history_{media_type}", len(data))
        for index, item in enumerate(data):
            uri = item.get("spotify_track_uri")
            rows.append(
                (
                    str(path.relative_to(ROOT)),
                    index,
                    media_type,
                    item.get("ts"),
                    item.get("ms_played"),
                    item.get("platform"),
                    item.get("conn_country"),
                    item.get("ip_addr"),
                    item.get("master_metadata_track_name"),
                    item.get("master_metadata_album_artist_name"),
                    item.get("master_metadata_album_album_name"),
                    uri,
                    spotify_id_from_uri(uri),
                    item.get("episode_name"),
                    item.get("episode_show_name"),
                    item.get("spotify_episode_uri"),
                    item.get("audiobook_title"),
                    item.get("audiobook_uri"),
                    item.get("audiobook_chapter_uri"),
                    item.get("audiobook_chapter_title"),
                    item.get("reason_start"),
                    item.get("reason_end"),
                    item.get("shuffle"),
                    item.get("skipped"),
                    item.get("offline"),
                    item.get("offline_timestamp"),
                    item.get("incognito_mode"),
                    jdump(item),
                )
            )
    conn.executemany(
        """
        INSERT INTO spotify_streams(
            source_file, source_index, media_type, played_at, ms_played, platform, conn_country, ip_addr,
            track_name, artist_name, album_name, spotify_track_uri, spotify_track_id, episode_name,
            episode_show_name, spotify_episode_uri, audiobook_title, audiobook_uri, audiobook_chapter_uri,
            audiobook_chapter_title, reason_start, reason_end, shuffle, skipped, offline, offline_timestamp,
            incognito_mode, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_lastfm(conn):
    for table, glob, kind in [
        ("lastfm_scrobbles", "scrobbles-*.json", "lastfm_scrobbles"),
        ("lastfm_loved_tracks", "lovedtracks-*.json", "lastfm_loved_tracks"),
    ]:
        for path in sorted((ROOT / "lastfm").glob(glob)):
            data = read_json(path)
            rows = []
            for page_index, item_index, item in flatten_pages(data):
                artist_name, artist_mbid = lastfm_artist(item)
                date = item.get("date") or {}
                album = item.get("album") or {}
                common = (
                    str(path.relative_to(ROOT)),
                    page_index,
                    item_index,
                    iso_from_uts(date.get("uts")),
                    date.get("#text"),
                    artist_name,
                    artist_mbid,
                )
                if table == "lastfm_scrobbles":
                    rows.append(
                        common
                        + (
                            album.get("#text"),
                            album.get("mbid"),
                            item.get("name"),
                            item.get("mbid"),
                            item.get("url"),
                            item.get("streamable"),
                            jdump(item.get("image")),
                            jdump(item),
                        )
                    )
                else:
                    rows.append(
                        common
                        + (
                            item.get("name"),
                            item.get("mbid"),
                            item.get("url"),
                            jdump(item.get("streamable")),
                            jdump(item.get("image")),
                            jdump(item),
                        )
                    )
            record_source(conn, path, kind, len(rows))
            if table == "lastfm_scrobbles":
                conn.executemany(
                    """
                    INSERT INTO lastfm_scrobbles(
                        source_file, page_index, item_index, played_at, played_at_text, artist_name, artist_mbid,
                        album_name, album_mbid, track_name, track_mbid, url, streamable, image_json, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            else:
                conn.executemany(
                    """
                    INSERT INTO lastfm_loved_tracks(
                        source_file, page_index, item_index, loved_at, loved_at_text, artist_name, artist_mbid,
                        track_name, track_mbid, url, streamable, image_json, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )


def artist_names(artists):
    return ", ".join(a.get("name") for a in artists or [] if a.get("name"))


def upsert_artist(conn, artist):
    if not artist or not artist.get("id"):
        return
    conn.execute(
        """
        INSERT INTO spotify_artists(artist_id, name, uri, href, spotify_url, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(artist_id) DO UPDATE SET
            name=excluded.name,
            uri=excluded.uri,
            href=excluded.href,
            spotify_url=excluded.spotify_url,
            raw_json=excluded.raw_json
        """,
        (artist.get("id"), artist.get("name"), artist.get("uri"), artist.get("href"), spotify_url(artist), jdump(artist)),
    )


def upsert_album(conn, album):
    if not album or not album.get("id"):
        return
    conn.execute(
        """
        INSERT INTO spotify_albums(
            album_id, name, album_type, release_date, release_date_precision, total_tracks,
            uri, href, spotify_url, images_json, available_markets_json, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(album_id) DO UPDATE SET
            name=excluded.name,
            album_type=excluded.album_type,
            release_date=excluded.release_date,
            release_date_precision=excluded.release_date_precision,
            total_tracks=excluded.total_tracks,
            uri=excluded.uri,
            href=excluded.href,
            spotify_url=excluded.spotify_url,
            images_json=excluded.images_json,
            available_markets_json=excluded.available_markets_json,
            raw_json=excluded.raw_json
        """,
        (
            album.get("id"),
            album.get("name"),
            album.get("album_type"),
            album.get("release_date"),
            album.get("release_date_precision"),
            album.get("total_tracks"),
            album.get("uri"),
            album.get("href"),
            spotify_url(album),
            jdump(album.get("images")),
            jdump(album.get("available_markets")),
            jdump(album),
        ),
    )
    for position, artist in enumerate(album.get("artists") or []):
        upsert_artist(conn, artist)
        conn.execute(
            """
            INSERT OR REPLACE INTO spotify_album_artists(album_id, artist_id, position, artist_name)
            VALUES (?, ?, ?, ?)
            """,
            (album.get("id"), artist.get("id"), position, artist.get("name")),
        )


def upsert_track(conn, track):
    if not track or not track.get("id"):
        return
    album = track.get("album") or {}
    upsert_album(conn, album)
    artists = track.get("artists") or []
    conn.execute(
        """
        INSERT INTO spotify_tracks(
            track_id, track_uri, href, name, artists_text, artists_json, album_id, album_name,
            duration_ms, explicit, popularity, preview_url, spotify_url, external_ids_json,
            available_markets_json, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
            track_uri=excluded.track_uri,
            href=excluded.href,
            name=excluded.name,
            artists_text=excluded.artists_text,
            artists_json=excluded.artists_json,
            album_id=excluded.album_id,
            album_name=excluded.album_name,
            duration_ms=excluded.duration_ms,
            explicit=excluded.explicit,
            popularity=excluded.popularity,
            preview_url=excluded.preview_url,
            spotify_url=excluded.spotify_url,
            external_ids_json=excluded.external_ids_json,
            available_markets_json=excluded.available_markets_json,
            raw_json=excluded.raw_json
        """,
        (
            track.get("id"),
            track.get("uri"),
            track.get("href"),
            track.get("name"),
            artist_names(artists),
            jdump(artists),
            album.get("id"),
            album.get("name"),
            track.get("duration_ms"),
            track.get("explicit"),
            track.get("popularity"),
            track.get("preview_url"),
            spotify_url(track),
            jdump(track.get("external_ids")),
            jdump(track.get("available_markets")),
            jdump(track),
        ),
    )
    for position, artist in enumerate(artists):
        upsert_artist(conn, artist)
        conn.execute(
            """
            INSERT OR REPLACE INTO spotify_track_artists(track_id, artist_id, position, artist_name)
            VALUES (?, ?, ?, ?)
            """,
            (track.get("id"), artist.get("id"), position, artist.get("name")),
        )


def insert_spotify_playlists(conn):
    playlists_path = ROOT / "spotify_playlists_export" / "playlists.json"
    playlists = read_json(playlists_path)
    record_source(conn, playlists_path, "spotify_playlist_entries", len(playlists))
    entry_rows = []
    for entry_index, playlist in enumerate(playlists):
        owner = playlist.get("owner") or {}
        tracks = playlist.get("tracks") or {}
        entry_rows.append(
            (
                entry_index,
                playlist.get("id"),
                playlist.get("name"),
                owner.get("id"),
                owner.get("display_name"),
                playlist.get("public"),
                playlist.get("collaborative"),
                playlist.get("snapshot_id"),
                tracks.get("total"),
                jdump(playlist),
            )
        )
    conn.executemany(
        """
        INSERT INTO spotify_playlist_entries(
            entry_index, playlist_id, name, owner_id, owner_display_name, public,
            collaborative, snapshot_id, tracks_total, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        entry_rows,
    )

    detail_paths = sorted((ROOT / "spotify_playlists_export" / "playlist_details").glob("*.json"))
    record_source(conn, ROOT / "spotify_playlists_export" / "summary.json", "spotify_playlist_summary", len(read_json(ROOT / "spotify_playlists_export" / "summary.json")))
    item_total = 0
    for path in detail_paths:
        playlist = read_json(path)
        owner = playlist.get("owner") or {}
        tracks = playlist.get("tracks") or {}
        conn.execute(
            """
            INSERT OR REPLACE INTO spotify_playlists(
                playlist_id, name, description, owner_id, owner_display_name, owner_uri, owner_url,
                public, collaborative, followers_total, snapshot_id, tracks_total, href, spotify_url,
                images_json, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playlist.get("id"),
                playlist.get("name"),
                playlist.get("description"),
                owner.get("id"),
                owner.get("display_name"),
                owner.get("uri"),
                spotify_url(owner),
                playlist.get("public"),
                playlist.get("collaborative"),
                (playlist.get("followers") or {}).get("total"),
                playlist.get("snapshot_id"),
                tracks.get("total"),
                playlist.get("href"),
                spotify_url(playlist),
                jdump(playlist.get("images")),
                jdump(playlist),
            ),
        )

    item_paths = sorted((ROOT / "spotify_playlists_export" / "playlist_items").glob("*.json"))
    for path in item_paths:
        playlist_id = path.stem
        items = read_json(path)
        item_total += len(items)
        rows = []
        for position, item in enumerate(items):
            track = item.get("track") or {}
            if isinstance(track, dict):
                upsert_track(conn, track)
            album = track.get("album") if isinstance(track, dict) else {}
            album = album or {}
            artists = track.get("artists") if isinstance(track, dict) else []
            album_artists = album.get("artists") or []
            added_by = item.get("added_by") or {}
            rows.append(
                (
                    playlist_id,
                    position,
                    item.get("added_at"),
                    added_by.get("id"),
                    added_by.get("display_name"),
                    added_by.get("uri"),
                    spotify_url(added_by),
                    item.get("is_local"),
                    item.get("primary_color"),
                    track.get("id"),
                    track.get("uri"),
                    track.get("href"),
                    track.get("type"),
                    track.get("name"),
                    artist_names(artists),
                    jdump(artists),
                    album.get("id"),
                    album.get("name"),
                    album.get("album_type"),
                    album.get("release_date"),
                    album.get("release_date_precision"),
                    artist_names(album_artists),
                    jdump(album_artists),
                    track.get("duration_ms"),
                    track.get("explicit"),
                    track.get("popularity"),
                    track.get("preview_url"),
                    spotify_url(track),
                    track.get("disc_number"),
                    track.get("track_number"),
                    track.get("is_playable"),
                    jdump(track) if track else None,
                    jdump(item),
                )
            )
        conn.executemany(
            """
            INSERT INTO spotify_playlist_items(
                playlist_id, position, added_at, added_by_id, added_by_display_name, added_by_uri,
                added_by_url, is_local, primary_color, track_id, track_uri, track_href, track_type,
                track_name, artists_text, artists_json, album_id, album_name, album_type,
                album_release_date, album_release_date_precision, album_artists_text, album_artists_json,
                duration_ms, explicit, popularity, preview_url, spotify_url, disc_number, track_number,
                is_playable, raw_track_json, raw_item_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    record_source(conn, ROOT / "spotify_playlists_export" / "playlist_items", "spotify_playlist_items", item_total)


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            create_schema(conn)
            insert_spotify_streams(conn)
            insert_lastfm(conn)
            insert_spotify_playlists(conn)
            conn.execute("ANALYZE")
    finally:
        conn.close()
    print(DB_PATH)


if __name__ == "__main__":
    main()
