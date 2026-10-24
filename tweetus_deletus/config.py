"""Configuration loading.

Config is a small TOML file (default: ~/.config/tweetus_deletus/config.toml)
holding your OAuth app credentials and defaults. Environment variables
override the file, which is handy for secrets:

    TWEETUS_CLIENT_ID, TWEETUS_CLIENT_SECRET, TWEETUS_REDIRECT_URI,
    TWEETUS_TOKEN_PATH
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass

from .errors import ConfigError

DEFAULT_CONFIG_PATH = "~/.config/tweetus_deletus/config.toml"
DEFAULT_TOKEN_PATH = "~/.config/tweetus_deletus/tokens.json"
DEFAULT_REDIRECT = "http://127.0.0.1:8721/callback"


@dataclass
class Config:
    client_id: str = ""
    client_secret: str | None = None
    redirect_uri: str = DEFAULT_REDIRECT
    token_path: str = DEFAULT_TOKEN_PATH
    # politeness: minimum seconds between API calls (0 = as fast as limits allow)
    min_interval: float = 0.0

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        path = os.path.expanduser(path or DEFAULT_CONFIG_PATH)
        data: dict = {}
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        app = data.get("app", data)  # allow a flat file or an [app] table

        cfg = cls(
            client_id=os.environ.get("TWEETUS_CLIENT_ID",
                                     app.get("client_id", "")),
            client_secret=os.environ.get("TWEETUS_CLIENT_SECRET",
                                         app.get("client_secret")) or None,
            redirect_uri=os.environ.get("TWEETUS_REDIRECT_URI",
                                        app.get("redirect_uri", DEFAULT_REDIRECT)),
            token_path=os.environ.get("TWEETUS_TOKEN_PATH",
                                      app.get("token_path", DEFAULT_TOKEN_PATH)),
            min_interval=float(app.get("min_interval", 0.0)),
        )
        return cfg

    def require_app(self) -> None:
        if not self.client_id:
            raise ConfigError(
                "No client_id configured. Create an OAuth 2.0 app at "
                "https://developer.x.com, then set client_id in your config "
                "file or the TWEETUS_CLIENT_ID environment variable. "
                "See README.md for the full setup.")
