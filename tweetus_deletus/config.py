"""Configuration loading and saving.

Config is a small TOML file (default: ~/.config/tweetus_deletus/config.toml)
holding your OAuth app credentials and defaults. The web UI's setup screen
writes it for you. Environment variables override the file, which is handy
for secrets:

    TWEETUS_CLIENT_ID, TWEETUS_CLIENT_SECRET, TWEETUS_REDIRECT_URI,
    TWEETUS_TOKEN_PATH
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass

from .errors import ConfigError

DEFAULT_CONFIG_PATH = "~/.config/tweetus_deletus/config.toml"
DEFAULT_REDIRECT = "http://127.0.0.1:8721/callback"
# Whoever builds the app can bake their X app's Client ID in here, so the
# people they give it to only ever see "Connect with X".
BUNDLED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "bundled.json")


def bundled_client_id() -> str:
    try:
        with open(BUNDLED_PATH, "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("client_id") or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


@dataclass
class Config:
    client_id: str = ""
    client_secret: str | None = None
    redirect_uri: str = DEFAULT_REDIRECT
    token_path: str = ""
    # politeness: minimum seconds between API calls (0 = as fast as limits allow)
    min_interval: float = 0.0
    path: str = ""
    # True when client_id came from the built-in bundled.json
    bundled: bool = False

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        path = os.path.expanduser(path or DEFAULT_CONFIG_PATH)
        data: dict = {}
        if os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    data = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"Can't read {path}: {exc}") from exc
        app = data.get("app", data)  # allow a flat file or an [app] table
        default_tokens = os.path.join(os.path.dirname(path), "tokens.json")
        client_id = (os.environ.get("TWEETUS_CLIENT_ID")
                     or app.get("client_id", ""))
        bundled = False
        if not client_id:
            client_id = bundled_client_id()
            bundled = bool(client_id)

        return cls(
            client_id=client_id,
            bundled=bundled,
            client_secret=os.environ.get("TWEETUS_CLIENT_SECRET",
                                         app.get("client_secret")) or None,
            redirect_uri=os.environ.get("TWEETUS_REDIRECT_URI",
                                        app.get("redirect_uri", DEFAULT_REDIRECT)),
            token_path=os.path.expanduser(os.environ.get(
                "TWEETUS_TOKEN_PATH", app.get("token_path", default_tokens))),
            min_interval=float(app.get("min_interval", 0.0)),
            path=path,
        )

    def save(self) -> None:
        """Write the [app] table back to `self.path` (0600: it may hold a
        client secret)."""
        def q(value: str) -> str:
            return json.dumps(value)  # a JSON string is a valid TOML string

        lines = ["# Written by Tweetus Deletus. Safe to edit by hand.", "",
                 "[app]"]
        if self.client_id and not self.bundled:
            lines.append(f"client_id = {q(self.client_id)}")
        if self.client_secret:
            lines.append(f"client_secret = {q(self.client_secret)}")
        lines.append(f"redirect_uri = {q(self.redirect_uri)}")
        lines.append(f"token_path = {q(self.token_path)}")
        lines.append(f"min_interval = {float(self.min_interval)}")

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def require_app(self) -> None:
        if not self.client_id:
            raise ConfigError(
                "No client_id configured. Create an OAuth 2.0 app at "
                "https://developer.x.com, then set client_id in your config "
                "file or the TWEETUS_CLIENT_ID environment variable. "
                "See README.md for the full setup.")
