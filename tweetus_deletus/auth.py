"""OAuth 2.0 Authorization Code flow with PKCE for the X API v2.

X's user-context write endpoints (delete post, unlike, unretweet, remove
bookmark, unfollow, unmute, unblock, delete DM, delete list) require an OAuth
2.0 user access token with the right scopes. This module runs the one-time
browser authorization, captures the redirect on a throwaway localhost server,
exchanges the code for tokens, stores them, and refreshes them when they
expire.

You create the OAuth 2.0 app once at https://developer.x.com, set the callback
URL to the one printed here (default http://127.0.0.1:8721/callback), and put
the client id (and client secret, for confidential apps) in your config.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from .errors import AuthError

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"

# Everything Tweetus Deletus can touch. offline.access gets us a refresh token
# so you only authorize in the browser once.
ALL_SCOPES = [
    "tweet.read",
    "tweet.write",
    "tweet.moderate.write",
    "users.read",
    "like.read",
    "like.write",
    "bookmark.read",
    "bookmark.write",
    "follows.read",
    "follows.write",
    "mute.read",
    "mute.write",
    "block.read",
    "block.write",
    "list.read",
    "list.write",
    "dm.read",
    "dm.write",
    "offline.access",
]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class _CallbackHandler(BaseHTTPRequestHandler):
    # Filled in by the server instance.
    result: dict = {}
    expected_state: str = ""

    def do_GET(self):  # noqa: N802 (name mandated by BaseHTTPRequestHandler)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        self.__class__.result = {k: v[0] for k, v in params.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in self.__class__.result and (
            self.__class__.result.get("state") == self.__class__.expected_state
        )
        body = (
            "<h2>Tweetus Deletus</h2><p>"
            + ("Authorized. You can close this tab and return to the terminal."
               if ok else
               "Authorization failed or state mismatch. Check the terminal.")
            + "</p>"
        )
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):  # silence the default stderr logging
        return


class TokenStore:
    """Reads/writes the OAuth tokens as JSON on disk (0600)."""

    def __init__(self, path: str):
        self.path = os.path.expanduser(path)

    def load(self) -> dict | None:
        if not os.path.exists(self.path):
            return None
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


class Authenticator:
    """Owns the token lifecycle: authorize once, then hand out fresh tokens."""

    def __init__(self, client_id: str, client_secret: str | None,
                 redirect_uri: str, store: TokenStore,
                 scopes: list[str] | None = None):
        if not client_id:
            raise AuthError("client_id is required (set it in your config).")
        self.client_id = client_id
        self.client_secret = client_secret or None
        self.redirect_uri = redirect_uri
        self.store = store
        self.scopes = scopes or ALL_SCOPES
        self._tokens = store.load()

    # -- public ---------------------------------------------------------

    @property
    def authorized(self) -> bool:
        return bool(self._tokens and self._tokens.get("refresh_token")
                    or self._tokens and self._tokens.get("access_token"))

    def access_token(self) -> str:
        """Return a valid access token, refreshing or authorizing as needed."""
        if not self._tokens:
            raise AuthError(
                "Not authorized yet. Run `tweetus-deletus login` first.")
        if self._expired():
            self._refresh()
        return self._tokens["access_token"]

    def login(self, open_browser: bool = True) -> dict:
        """Run the interactive browser authorization once."""
        verifier = _b64url(secrets.token_bytes(64))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = secrets.token_urlsafe(24)

        auth_params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(auth_params)

        code = self._capture_code(url, state, open_browser)
        self._exchange_code(code, verifier)
        return self._tokens

    # -- internals ------------------------------------------------------

    def _capture_code(self, url: str, state: str, open_browser: bool) -> str:
        parsed = urllib.parse.urlparse(self.redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80

        _CallbackHandler.result = {}
        _CallbackHandler.expected_state = state
        httpd = HTTPServer((host, port), _CallbackHandler)
        httpd.timeout = 300

        print("\nOpen this URL in your browser to authorize Tweetus Deletus:\n")
        print("  " + url + "\n")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        print(f"Waiting for the redirect to {self.redirect_uri} ...")

        # Serve requests until we get our callback or time out.
        done = threading.Event()

        def serve():
            while not done.is_set():
                httpd.handle_request()
                if _CallbackHandler.result:
                    done.set()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        if not done.wait(timeout=300):
            httpd.server_close()
            raise AuthError("Timed out waiting for browser authorization.")
        httpd.server_close()

        result = _CallbackHandler.result
        if "error" in result:
            raise AuthError(
                f"Authorization denied: {result.get('error')} "
                f"{result.get('error_description', '')}".strip())
        if result.get("state") != state:
            raise AuthError("State mismatch — possible CSRF, aborting.")
        code = result.get("code")
        if not code:
            raise AuthError("No authorization code returned.")
        return code

    def _token_request(self, data: dict) -> dict:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        auth = None
        # Confidential clients authenticate with HTTP Basic; public (PKCE-only)
        # clients send the client_id in the body instead.
        if self.client_secret:
            auth = (self.client_id, self.client_secret)
        else:
            data = {**data, "client_id": self.client_id}
        resp = requests.post(TOKEN_URL, data=data, headers=headers, auth=auth,
                             timeout=30)
        if resp.status_code != 200:
            raise AuthError(
                f"Token endpoint returned {resp.status_code}: {resp.text}")
        return resp.json()

    def _exchange_code(self, code: str, verifier: str) -> None:
        payload = self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
        })
        self._persist(payload)

    def _refresh(self) -> None:
        refresh = self._tokens.get("refresh_token")
        if not refresh:
            raise AuthError(
                "Access token expired and no refresh token is stored. "
                "Run `tweetus-deletus login` again.")
        payload = self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        })
        self._persist(payload)

    def _persist(self, payload: dict) -> None:
        now = int(time.time())
        tokens = dict(self._tokens or {})
        tokens["access_token"] = payload["access_token"]
        # A refresh may or may not return a new refresh token; keep the old one
        # if it doesn't.
        if payload.get("refresh_token"):
            tokens["refresh_token"] = payload["refresh_token"]
        tokens["scope"] = payload.get("scope", tokens.get("scope"))
        tokens["token_type"] = payload.get("token_type", "bearer")
        tokens["expires_at"] = now + int(payload.get("expires_in", 7200))
        self._tokens = tokens
        self.store.save(tokens)

    def _expired(self) -> bool:
        # Refresh a minute early to avoid racing the clock.
        return int(time.time()) >= int(self._tokens.get("expires_at", 0)) - 60
