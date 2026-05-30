#!/usr/bin/env python3
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "music.db"


BRACKET_RE = re.compile(r"[\[(].*?(?:remaster(?:ed)?|deluxe|anniversary|bonus|explicit|radio edit|single version|live).*?[\])]", re.I)
FEAT_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$", re.I)
PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
SPACE_RE = re.compile(r"\s+")
GENERIC_TITLES = {
    "intro",
    "outro",
    "interlude",
    "prelude",
    "theme",
    "home",
    "one",
    "love",
    "you",
    "me",
    "us",
    "live",
    "untitled",
    "bonus track",
}


def norm(value):
    if not value:
        return None
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    value = value.replace("&", " and ")
    value = FEAT_RE.sub("", value)
    value = BRACKET_RE.sub("", value)
    value = PUNCT_RE.sub(" ", value)
    value = SPACE_RE.sub(" ", value).strip()
    return value or None


def key(artist, track, album=None):
    artist_key = norm(artist)
    track_key = norm(track)
    album_key = norm(album)
    if not artist_key or not track_key:
        return None
    return f"{artist_key}|{track_key}|{album_key or ''}"


def simple_key(artist, track):
    artist_key = norm(artist)
    track_key = norm(track)
    if not artist_key or not track_key:
        return None
    return f"{artist_key}|{track_key}"


def safe_loose_title(loose):
    if not loose or "|" not in loose:
        return False
    _, title = loose.split("|", 1)
    if title in GENERIC_TITLES:
        return False
    if len(title) < 4 and " " not in title:
        return False
    return True


def create_schema(conn):
    conn.executescript(
        """
        DROP TABLE IF EXISTS canonical_track_links;
        DROP TABLE IF EXISTS canonical_track_events;
        DROP TABLE IF EXISTS canonical_tracks;
        DROP VIEW IF EXISTS canonical_track_summary;

        CREATE TABLE canonical_tracks (
            canonical_track_id INTEGER PRIMARY KEY,
            primary_artist_name TEXT,
            primary_track_name TEXT,
            primary_album_name TEXT,
            norm_artist TEXT,
            norm_track TEXT,
            norm_album TEXT,
            spotify_track_id TEXT,
            spotify_track_uri TEXT,
            lastfm_track_mbid TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            source_count INTEGER NOT NULL DEFAULT 0,
            listen_count INTEGER NOT NULL DEFAULT 0,
            spotify_listen_count INTEGER NOT NULL DEFAULT 0,
            lastfm_scrobble_count INTEGER NOT NULL DEFAULT 0,
            loved_count INTEGER NOT NULL DEFAULT 0,
            playlist_item_count INTEGER NOT NULL DEFAULT 0,
            confidence_note TEXT
        );

        CREATE TABLE canonical_track_links (
            canonical_track_id INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            link_value TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_count INTEGER NOT NULL,
            PRIMARY KEY (link_type, link_value)
        );

        CREATE TABLE canonical_track_events (
            id INTEGER PRIMARY KEY,
            canonical_track_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            source TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            event_at TEXT,
            artist_name TEXT,
            track_name TEXT,
            album_name TEXT,
            spotify_track_id TEXT,
            spotify_track_uri TEXT,
            lastfm_track_mbid TEXT,
            playlist_id TEXT,
            match_method TEXT NOT NULL,
            confidence REAL NOT NULL
        );

        CREATE INDEX idx_canonical_tracks_spotify ON canonical_tracks(spotify_track_id);
        CREATE INDEX idx_canonical_tracks_norm ON canonical_tracks(norm_artist, norm_track, norm_album);
        CREATE INDEX idx_canonical_events_track ON canonical_track_events(canonical_track_id);
        CREATE INDEX idx_canonical_events_source ON canonical_track_events(source, source_table, source_id);
        CREATE INDEX idx_canonical_events_time ON canonical_track_events(event_at);

        CREATE VIEW canonical_track_summary AS
        SELECT
            canonical_track_id,
            primary_artist_name,
            primary_track_name,
            primary_album_name,
            spotify_track_id,
            lastfm_track_mbid,
            listen_count,
            spotify_listen_count,
            lastfm_scrobble_count,
            loved_count,
            playlist_item_count,
            first_seen_at,
            last_seen_at,
            confidence_note
        FROM canonical_tracks
        ORDER BY listen_count DESC, loved_count DESC, playlist_item_count DESC;
        """
    )


def fetch_event_rows(conn):
    for row in conn.execute(
        """
        SELECT
            'spotify_stream' AS event_type,
            'spotify' AS source,
            'spotify_streams' AS source_table,
            id AS source_id,
            played_at AS event_at,
            artist_name,
            track_name,
            album_name,
            spotify_track_id,
            spotify_track_uri,
            NULL AS lastfm_track_mbid,
            NULL AS playlist_id
        FROM spotify_streams
        WHERE track_name IS NOT NULL
        """
    ):
        yield dict(row)

    for row in conn.execute(
        """
        SELECT
            'lastfm_scrobble' AS event_type,
            'lastfm' AS source,
            'lastfm_scrobbles' AS source_table,
            id AS source_id,
            played_at AS event_at,
            artist_name,
            track_name,
            album_name,
            NULL AS spotify_track_id,
            NULL AS spotify_track_uri,
            track_mbid AS lastfm_track_mbid,
            NULL AS playlist_id
        FROM lastfm_scrobbles
        """
    ):
        yield dict(row)

    for row in conn.execute(
        """
        SELECT
            'lastfm_loved' AS event_type,
            'lastfm' AS source,
            'lastfm_loved_tracks' AS source_table,
            id AS source_id,
            loved_at AS event_at,
            artist_name,
            track_name,
            NULL AS album_name,
            NULL AS spotify_track_id,
            NULL AS spotify_track_uri,
            track_mbid AS lastfm_track_mbid,
            NULL AS playlist_id
        FROM lastfm_loved_tracks
        """
    ):
        yield dict(row)

    for row in conn.execute(
        """
        SELECT
            'playlist_item' AS event_type,
            'spotify' AS source,
            'spotify_playlist_items' AS source_table,
            id AS source_id,
            added_at AS event_at,
            artists_text AS artist_name,
            track_name,
            album_name,
            track_id AS spotify_track_id,
            track_uri AS spotify_track_uri,
            NULL AS lastfm_track_mbid,
            playlist_id
        FROM spotify_playlist_items
        WHERE track_name IS NOT NULL
        """
    ):
        yield dict(row)


class Resolver:
    def __init__(self):
        self.parent = {}

    def add(self, item):
        self.parent.setdefault(item, item)

    def find(self, item):
        self.add(item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a


def build_groups(events):
    resolver = Resolver()
    spotify_keys = defaultdict(list)
    mbid_keys = defaultdict(list)
    full_keys = defaultdict(list)
    loose_keys = defaultdict(list)

    for i, event in enumerate(events):
        resolver.add(i)
        if event["spotify_track_id"]:
            spotify_keys[event["spotify_track_id"]].append(i)
        if event["lastfm_track_mbid"]:
            mbid_keys[event["lastfm_track_mbid"]].append(i)
        full = key(event["artist_name"], event["track_name"], event["album_name"])
        loose = simple_key(event["artist_name"], event["track_name"])
        event["full_key"] = full
        event["loose_key"] = loose
        if full:
            full_keys[full].append(i)
        if loose:
            loose_keys[loose].append(i)

    for indexes in spotify_keys.values():
        for index in indexes[1:]:
            resolver.union(indexes[0], index)
    for indexes in mbid_keys.values():
        for index in indexes[1:]:
            resolver.union(indexes[0], index)
    for indexes in full_keys.values():
        for index in indexes[1:]:
            resolver.union(indexes[0], index)

    safe_loose = {}
    for loose, indexes in loose_keys.items():
        spotify_ids = {events[i]["spotify_track_id"] for i in indexes if events[i]["spotify_track_id"]}
        mbids = {events[i]["lastfm_track_mbid"] for i in indexes if events[i]["lastfm_track_mbid"]}
        albums = {norm(events[i]["album_name"]) for i in indexes if norm(events[i]["album_name"])}
        if len(spotify_ids) <= 1 and len(mbids) <= 1 and len(albums) <= 3:
            safe_loose[loose] = indexes
        elif safe_loose_title(loose) and len(spotify_ids) <= 25 and len(mbids) <= 8:
            safe_loose[loose] = indexes
    for indexes in safe_loose.values():
        for index in indexes[1:]:
            resolver.union(indexes[0], index)

    groups = defaultdict(list)
    for i in range(len(events)):
        groups[resolver.find(i)].append(i)
    return groups


def pick(counter):
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def match_method(event):
    if event["spotify_track_id"]:
        return "spotify_track_id"
    if event["lastfm_track_mbid"]:
        return "lastfm_track_mbid"
    if event["full_key"]:
        return "normalized_artist_track_album"
    return "normalized_artist_track"


def confidence(method):
    return {
        "spotify_track_id": 1.0,
        "lastfm_track_mbid": 0.96,
        "normalized_artist_track_album": 0.88,
        "normalized_artist_track": 0.72,
    }[method]


def insert_group(conn, canonical_id, events, indexes):
    group_events = [events[i] for i in indexes]
    artists = Counter(e["artist_name"] for e in group_events if e["artist_name"])
    tracks = Counter(e["track_name"] for e in group_events if e["track_name"])
    albums = Counter(e["album_name"] for e in group_events if e["album_name"])
    spotify_ids = Counter(e["spotify_track_id"] for e in group_events if e["spotify_track_id"])
    spotify_uris = Counter(e["spotify_track_uri"] for e in group_events if e["spotify_track_uri"])
    mbids = Counter(e["lastfm_track_mbid"] for e in group_events if e["lastfm_track_mbid"])
    event_times = [e["event_at"] for e in group_events if e["event_at"]]
    sources = {e["source"] for e in group_events}
    methods = {match_method(e) for e in group_events}
    listen_count = sum(e["event_type"] in ("spotify_stream", "lastfm_scrobble") for e in group_events)
    spotify_listen_count = sum(e["event_type"] == "spotify_stream" for e in group_events)
    lastfm_scrobble_count = sum(e["event_type"] == "lastfm_scrobble" for e in group_events)
    loved_count = sum(e["event_type"] == "lastfm_loved" for e in group_events)
    playlist_item_count = sum(e["event_type"] == "playlist_item" for e in group_events)

    primary_artist = pick(artists)
    primary_track = pick(tracks)
    primary_album = pick(albums)
    spotify_id = pick(spotify_ids)
    spotify_uri = pick(spotify_uris)
    mbid = pick(mbids)
    note = ",".join(sorted(methods))

    conn.execute(
        """
        INSERT INTO canonical_tracks(
            canonical_track_id, primary_artist_name, primary_track_name, primary_album_name,
            norm_artist, norm_track, norm_album, spotify_track_id, spotify_track_uri,
            lastfm_track_mbid, first_seen_at, last_seen_at, source_count, listen_count,
            spotify_listen_count, lastfm_scrobble_count, loved_count, playlist_item_count,
            confidence_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            canonical_id,
            primary_artist,
            primary_track,
            primary_album,
            norm(primary_artist),
            norm(primary_track),
            norm(primary_album),
            spotify_id,
            spotify_uri,
            mbid,
            min(event_times) if event_times else None,
            max(event_times) if event_times else None,
            len(sources),
            listen_count,
            spotify_listen_count,
            lastfm_scrobble_count,
            loved_count,
            playlist_item_count,
            note,
        ),
    )

    links = []
    for value, count in spotify_ids.items():
        links.append((canonical_id, "spotify_track_id", value, 1.0, count))
    for value, count in spotify_uris.items():
        links.append((canonical_id, "spotify_track_uri", value, 1.0, count))
    for value, count in mbids.items():
        links.append((canonical_id, "lastfm_track_mbid", value, 0.96, count))
    for value, count in Counter(e["full_key"] for e in group_events if e["full_key"]).items():
        links.append((canonical_id, "normalized_artist_track_album", value, 0.88, count))
    for value, count in Counter(e["loose_key"] for e in group_events if e["loose_key"]).items():
        links.append((canonical_id, "normalized_artist_track", value, 0.72, count))
    conn.executemany(
        """
        INSERT OR IGNORE INTO canonical_track_links(
            canonical_track_id, link_type, link_value, confidence, evidence_count
        ) VALUES (?, ?, ?, ?, ?)
        """,
        links,
    )

    event_rows = []
    for e in group_events:
        method = match_method(e)
        event_rows.append(
            (
                canonical_id,
                e["event_type"],
                e["source"],
                e["source_table"],
                e["source_id"],
                e["event_at"],
                e["artist_name"],
                e["track_name"],
                e["album_name"],
                e["spotify_track_id"],
                e["spotify_track_uri"],
                e["lastfm_track_mbid"],
                e["playlist_id"],
                method,
                confidence(method),
            )
        )
    conn.executemany(
        """
        INSERT INTO canonical_track_events(
            canonical_track_id, event_type, source, source_table, source_id, event_at,
            artist_name, track_name, album_name, spotify_track_id, spotify_track_uri,
            lastfm_track_mbid, playlist_id, match_method, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        event_rows,
    )


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            create_schema(conn)
            events = list(fetch_event_rows(conn))
            groups = build_groups(events)
            for canonical_id, indexes in enumerate(groups.values(), 1):
                insert_group(conn, canonical_id, events, indexes)
            conn.execute("ANALYZE")
        print(f"events: {len(events)}")
        print(f"canonical_tracks: {len(groups)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
