#!/usr/bin/env python3
import base64
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
SCOPES = "playlist-read-private playlist-read-collaborative"
TOKEN_PATH = Path(".spotify_token.json")


def load_dotenv(path=Path(".env")):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing env var: {name}")
    return value


def post_form(url, data, headers):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())


def get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())


def save_token(token):
    token = dict(token)
    token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600)) - 60
    TOKEN_PATH.write_text(json.dumps(token, indent=2), encoding="utf-8")
    TOKEN_PATH.chmod(0o600)
    return token


def token_headers(client_id, client_secret):
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def refresh_token(client_id, client_secret, refresh):
    token = post_form(
        TOKEN_URL,
        {"grant_type": "refresh_token", "refresh_token": refresh},
        token_headers(client_id, client_secret),
    )
    token.setdefault("refresh_token", refresh)
    return save_token(token)


def get_access_token(client_id, client_secret, redirect_uri):
    if TOKEN_PATH.exists():
        token = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        if token.get("access_token") and token.get("expires_at", 0) > time.time():
            return token["access_token"]
        if token.get("refresh_token"):
            return refresh_token(client_id, client_secret, token["refresh_token"])["access_token"]

    state = secrets.token_urlsafe(18)
    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"
    print(auth_url, flush=True)
    webbrowser.open(auth_url)

    code = wait_for_code(redirect_uri, state)
    token = post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        token_headers(client_id, client_secret),
    )
    return save_token(token)["access_token"]


def page_all(url, token):
    items = []
    while url:
        data = get_json(url, token)
        items.extend(data.get("items", []))
        url = data.get("next")
    return items


def wait_for_code(redirect_uri, expected_state):
    parsed = urllib.parse.urlparse(redirect_uri)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = query.get("code", [None])[0]
            result["state"] = query.get("state", [None])[0]
            result["error"] = query.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Spotify auth received. You can close this tab.")

    server = HTTPServer((parsed.hostname, parsed.port), Handler)
    server.handle_request()
    server.server_close()

    if result.get("error"):
        raise SystemExit(f"spotify auth error: {result['error']}")
    if result.get("state") != expected_state:
        raise SystemExit("spotify auth state mismatch")
    if not result.get("code"):
        raise SystemExit("spotify auth did not return a code")
    return result["code"]


def main():
    load_dotenv()
    client_id = require_env("SPOTIFY_CLIENT_ID")
    client_secret = require_env("SPOTIFY_CLIENT_SECRET")
    redirect_uri = require_env("SPOTIFY_REDIRECT_URI")
    token = get_access_token(client_id, client_secret, redirect_uri)

    out_dir = Path("spotify_playlists_export")
    items_dir = out_dir / "playlist_items"
    detail_dir = out_dir / "playlist_details"
    items_dir.mkdir(parents=True, exist_ok=True)
    detail_dir.mkdir(parents=True, exist_ok=True)

    playlists = page_all(f"{API_URL}/me/playlists?limit=50", token)
    (out_dir / "playlists.json").write_text(json.dumps(playlists, indent=2), encoding="utf-8")

    summary = []
    errors = []
    for index, playlist in enumerate(playlists, 1):
        playlist_id = playlist["id"]
        print(f"{index}/{len(playlists)} {playlist.get('name')} ({playlist_id})", flush=True)
        try:
            detail = get_json(f"{API_URL}/playlists/{playlist_id}", token)
            (detail_dir / f"{playlist_id}.json").write_text(json.dumps(detail, indent=2), encoding="utf-8")
            url = f"{API_URL}/playlists/{playlist_id}/tracks?limit=50"
            tracks = page_all(url, token)
            (items_dir / f"{playlist_id}.json").write_text(json.dumps(tracks, indent=2), encoding="utf-8")
            summary.append(
                {
                    "id": playlist_id,
                    "name": detail.get("name"),
                    "description": detail.get("description"),
                    "owner": detail.get("owner"),
                    "followers": detail.get("followers"),
                    "public": detail.get("public"),
                    "collaborative": detail.get("collaborative"),
                    "snapshot_id": detail.get("snapshot_id"),
                    "track_count": len(tracks),
                    "spotify_url": ((detail.get("external_urls") or {}).get("spotify")),
                }
            )
        except Exception as exc:
            errors.append({"id": playlist_id, "name": playlist.get("name"), "error": str(exc)})

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")
    print(f"wrote {len(summary)} playlists, {len(errors)} errors to {out_dir}", flush=True)


if __name__ == "__main__":
    main()
