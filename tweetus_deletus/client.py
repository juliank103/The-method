"""A thin X API v2 client: auth header injection, pagination, rate-limit aware.

Only the verbs Tweetus Deletus needs (GET, DELETE, PUT, POST) are exposed. The
client refreshes the access token through the Authenticator as needed, and
honours X's 15-minute rate-limit windows by reading the `x-rate-limit-*`
response headers and sleeping when a window is exhausted (or when the API
returns 429).
"""

from __future__ import annotations

import time
from typing import Callable, Iterator

import requests

from .auth import Authenticator
from .errors import ApiError

BASE = "https://api.x.com"


class XClient:
    def __init__(self, auth: Authenticator, *, dry_run: bool = False,
                 min_interval: float = 0.0,
                 sleeper: Callable[[float], None] = time.sleep,
                 logger: Callable[[str], None] = print):
        self.auth = auth
        self.dry_run = dry_run
        self.min_interval = min_interval  # polite spacing between calls, seconds
        self._sleep = sleeper
        self._log = logger
        self._session = requests.Session()
        self._last_call = 0.0

    # -- low level ------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.auth.access_token()}",
            "User-Agent": "tweetus-deletus/1.0",
        }

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            self._sleep(wait)

    def request(self, method: str, path: str, *, params=None, json=None,
                retries: int = 5):
        """Perform a request, transparently handling 429 and 5xx with backoff.

        `path` is the part after the host, e.g. "/2/users/me".
        """
        url = BASE + path
        attempt = 0
        while True:
            self._throttle()
            self._last_call = time.time()
            try:
                resp = self._session.request(
                    method, url, headers=self._headers(), params=params,
                    json=json, timeout=30)
            except requests.RequestException as exc:
                if attempt >= retries:
                    raise ApiError(f"Couldn't reach X: {exc}") from exc
                backoff = min(2 ** attempt, 60)
                self._log(f"Network problem ({exc.__class__.__name__}); "
                          f"retrying in {backoff}s")
                self._sleep(backoff)
                attempt += 1
                continue

            if resp.status_code == 429:
                self._handle_rate_limit(resp, attempt, retries, method, path)
                attempt += 1
                continue
            if resp.status_code in (500, 502, 503, 504) and attempt < retries:
                backoff = min(2 ** attempt, 60)
                self._log(f"  {resp.status_code} from X; retrying in {backoff}s")
                self._sleep(backoff)
                attempt += 1
                continue

            # Proactively pause when we've drained the window but still got 200.
            self._maybe_pause_on_header(resp)
            return resp

    def _handle_rate_limit(self, resp, attempt, retries, method, path):
        if attempt >= retries:
            raise ApiError(
                f"Rate limited repeatedly on {method} {path}; giving up.",
                status=429)
        reset = resp.headers.get("x-rate-limit-reset")
        now = time.time()
        if reset:
            wait = max(1.0, float(reset) - now) + 1.0
        else:
            wait = min(2 ** attempt * 5, 900)
        self._log(f"Hit X's rate limit; waiting {_human_wait(wait)} "
                  f"(resumes around {_clock(now + wait)}).")
        self._sleep(wait)

    def _maybe_pause_on_header(self, resp):
        remaining = resp.headers.get("x-rate-limit-remaining")
        reset = resp.headers.get("x-rate-limit-reset")
        if remaining is None or reset is None:
            return
        try:
            if int(remaining) <= 0:
                wait = max(1.0, float(reset) - time.time()) + 1.0
                self._log(f"Used up this rate-limit window; waiting "
                          f"{_human_wait(wait)} (resumes around "
                          f"{_clock(time.time() + wait)}).")
                self._sleep(wait)
        except ValueError:
            pass

    # -- typed helpers --------------------------------------------------

    def get(self, path, params=None) -> dict:
        resp = self.request("GET", path, params=params)
        if resp.status_code >= 400:
            raise ApiError(f"GET {path} -> {resp.status_code}: {resp.text}",
                           status=resp.status_code, payload=_safe_json(resp))
        return _safe_json(resp)

    def delete(self, path) -> dict:
        """DELETE a resource. In dry-run mode this is a no-op that reports ok."""
        if self.dry_run:
            return {"_dry_run": True, "path": path}
        resp = self.request("DELETE", path)
        if resp.status_code == 404:
            # Already gone — treat as success (idempotent delete).
            return {"_already_gone": True, "path": path}
        if resp.status_code >= 400:
            raise ApiError(f"DELETE {path} -> {resp.status_code}: {resp.text}",
                           status=resp.status_code, payload=_safe_json(resp))
        return _safe_json(resp)

    def paginate(self, path, params=None, *, max_items: int | None = None,
                 max_pages: int | None = None) -> Iterator[dict]:
        """Yield individual items across all pages of a v2 list endpoint.

        Follows `meta.next_token`. Includes the `includes` expansion payload on
        each yielded item under the key "_includes" so callers can resolve
        referenced tweets/users without a second lookup.
        """
        params = dict(params or {})
        seen = 0
        pages = 0
        while True:
            page = self.get(path, params=params)
            includes = page.get("includes", {})
            for item in page.get("data", []) or []:
                item["_includes"] = includes
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            pages += 1
            if max_pages is not None and pages >= max_pages:
                return
            next_token = (page.get("meta") or {}).get("next_token")
            if not next_token:
                return
            params["pagination_token"] = next_token


def _safe_json(resp) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {}


def _human_wait(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    return f"{round(seconds / 60)} min"


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))
