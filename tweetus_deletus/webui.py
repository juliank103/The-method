"""The Tweetus Deletus web UI: a small local server plus a single page app.

Run `tweetus-deletus` (or `tweetus-deletus ui`) and it opens in your browser
at http://127.0.0.1:8721. The server only listens on this computer.

Because a local server could in principle be poked by other websites you
visit, every /api request must carry a random per-launch token that only
the page itself knows, the Host header must be 127.0.0.1 or localhost
(blocks DNS-rebinding), state-changing requests must come from our own
origin, and the page refuses to be framed (blocks click-jacking).
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import mimetypes
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import webbrowser
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .actions import Actions
from .archive import ArchiveLog, ArchiveReader
from .auth import Authenticator, TokenStore
from .client import XClient
from .config import Config, DEFAULT_CONFIG_PATH
from .demo import DemoActions
from .errors import ApiError, AuthError, ConfigError, TweetusError
from .filters import FilterSet, extract_id, filters_from_dict
from .jobs import (JobManager, TYPE_CATEGORY, item_json, run_delete,
                   run_scan)
from .store import DataDir

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
MAX_JSON_BODY = 5 * 1024 * 1024
LOGIN_TTL = 15 * 60


class UserError(Exception):
    """Something the person can fix; shown as-is with HTTP 400."""


class App:
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH,
                 demo: bool = False):
        self.config_path = os.path.expanduser(config_path)
        self.demo = demo
        if demo:
            # Keep demo history/keep-lists/archives away from your real ones.
            self.data = DataDir(tempfile.mkdtemp(prefix="tweetus-demo-"))
            self._demo = DemoActions()
        else:
            self.data = DataDir(os.path.dirname(self.config_path))
            self._demo = None
        self.token = secrets.token_urlsafe(32)
        self.jobs = JobManager()
        self.port = 0
        self._auth: Authenticator | None = None
        self._me: dict | None = None
        self._me_error: str | None = None
        self._me_checked = 0.0
        self._logins: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # -- account plumbing -------------------------------------------------

    def config(self) -> Config:
        return Config.load(self.config_path)

    def auth(self) -> Authenticator:
        # One shared Authenticator: X refresh tokens are single-use, so two
        # instances refreshing at once would sign you out.
        with self._lock:
            if self._auth is None:
                cfg = self.config()
                cfg.require_app()
                self._auth = Authenticator(
                    cfg.client_id, cfg.client_secret, cfg.redirect_uri,
                    TokenStore(cfg.token_path))
            return self._auth

    def reset_auth(self) -> None:
        with self._lock:
            self._auth = None
            self._me = None
            self._me_error = None
            self._me_checked = 0.0

    def connected(self) -> bool:
        if self.demo:
            return True
        try:
            return self.auth().authorized
        except (ConfigError, AuthError):
            return False

    def actions(self, job=None) -> Actions:
        if self.demo:
            return self._demo
        cfg = self.config()
        client = XClient(
            self.auth(), min_interval=cfg.min_interval,
            sleeper=job.sleep if job else time.sleep,
            logger=job.say if job else (lambda msg: None))
        actions = Actions(client)
        if self._me:
            actions._me = {"id": self._me["id"],
                           "username": self._me.get("username", "")}
        return actions

    def me(self, refresh: bool = False) -> dict | None:
        if self.demo:
            return {"id": "1000000001", "username": "you_demo",
                    "name": "Demo Account"}
        if self._me and not refresh:
            return self._me
        if not self.connected():
            return None
        # Don't hammer X (and your API credits) if the lookup keeps failing.
        if not refresh and time.time() - self._me_checked < 60:
            return self._me
        self._me_checked = time.time()
        try:
            data = self.actions().client.get(
                "/2/users/me",
                {"user.fields": "username,name,profile_image_url"})["data"]
            self._me = data
            self._me_error = None
        except (ApiError, AuthError) as exc:
            self._me_error = str(exc)[:300]
        return self._me

    # -- status -----------------------------------------------------------

    def status(self) -> dict:
        try:
            cfg = self.config()
            config_error = None
        except ConfigError as exc:
            cfg = Config(path=self.config_path)
            config_error = str(exc)
        connected = self.connected()
        me = self.me() if connected else None
        rule = self.data.auto_rule()
        job = self.jobs.current
        return {
            "version": __version__,
            "demo": self.demo,
            "configured": bool(cfg.client_id) or self.demo,
            "config_error": config_error,
            "client_id": cfg.client_id,
            "has_secret": bool(cfg.client_secret),
            "redirect_uri": cfg.redirect_uri,
            "min_interval": cfg.min_interval,
            "connected": connected,
            "me": me,
            "me_error": self._me_error,
            "archive": self.data.archive_summary(),
            "job": job.to_dict() if job else None,
            "last_scan_id": self.jobs.last_scan.id if self.jobs.last_scan else None,
            "auto": self._auto_view(rule),
            "keep_count": len(self.data.keep_list()),
            "history_count": len(self.data.all_history()),
            "data_dir": self.data.root,
        }

    # -- auto-delete --------------------------------------------------------

    def _auto_view(self, rule: dict) -> dict:
        view = dict(rule)
        interval = float(rule.get("interval_hours") or 6) * 3600
        last = rule.get("last_run")
        view["next_run"] = (last + interval if last else time.time()) \
            if rule.get("enabled") else None
        return view

    def scheduler_loop(self) -> None:
        while not self._stop.wait(20):
            try:
                self.maybe_run_auto()
            except Exception:
                traceback.print_exc()

    def maybe_run_auto(self, force: bool = False):
        rule = self.data.auto_rule()
        if not force and not rule.get("enabled"):
            return None
        if self.jobs.busy() or not self.connected():
            if force:
                raise UserError("Can't start now: another task is running "
                                "or your account isn't connected.")
            return None
        interval = float(rule.get("interval_hours") or 6) * 3600
        last = rule.get("last_run")
        if not force and last and time.time() - last < interval:
            return None

        # Stamp the run up front so a failing rule doesn't retry every 20s.
        self.data.save_auto_rule({"last_run": time.time()})

        def work(job):
            result = {"ok": False, "matched": 0, "deleted": 0, "errors": 0}
            try:
                fs = FilterSet(
                    older_than_days=float(rule["older_than_days"]),
                    max_likes=rule.get("max_likes"),
                    protect_pinned=bool(rule.get("keep_pinned", True)),
                    protect_keywords=list(rule.get("keep_keywords") or []))
                actions = self.actions(job)
                run_scan(job, actions, list(rule["types"]), fs,
                         keep_ids=self.data.keep_ids())
                result["matched"] = len(job.items)
                if job.items:
                    log = ArchiveLog(self.data.history_path)
                    try:
                        run_delete(job, actions, list(job.items), log)
                    finally:
                        log.close()
                    result["deleted"] = job.stats.get("deleted", 0)
                    result["errors"] = job.stats.get("errors", 0)
                else:
                    job.say("Nothing to delete this time.")
                result["ok"] = True
            except Exception as exc:
                result["message"] = str(exc) or exc.__class__.__name__
                raise
            finally:
                result["at"] = time.time()
                self.data.save_auto_rule({"last_result": result})

        return self.jobs.start("auto", "Auto-delete", work)

    # -- OAuth ------------------------------------------------------------

    def begin_login(self) -> str:
        url, state, verifier = self.auth().begin_login()
        now = time.time()
        with self._lock:
            self._logins = {s: v for s, v in self._logins.items()
                            if now - v[1] < LOGIN_TTL}
            self._logins[state] = (verifier, now)
        return url

    def complete_login(self, code: str, state: str) -> None:
        with self._lock:
            pending = self._logins.pop(state, None)
        if not pending or time.time() - pending[1] > LOGIN_TTL:
            raise UserError("That sign-in link expired. Please try again.")
        self.auth().complete_login(code, pending[0])
        self._me = None
        self.me(refresh=True)


def _archive_summary(path: str, filename: str) -> dict:
    reader = ArchiveReader(path)
    counts = {}
    newest = oldest = None
    for category in ArchiveReader.GROUPS:
        n = 0
        for item in reader.items(category):
            n += 1
            if category == "posts" and item.created_at:
                if newest is None or item.created_at > newest:
                    newest = item.created_at
                if oldest is None or item.created_at < oldest:
                    oldest = item.created_at
        counts[category] = n
    return {
        "account": reader.account(),
        "counts": counts,
        "newest": newest.isoformat() if newest else None,
        "oldest": oldest.isoformat() if oldest else None,
        "size": os.path.getsize(path),
        "filename": filename,
        "uploaded": datetime.now(timezone.utc).isoformat(),
    }


# -- HTTP layer -----------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by make_server
    server_version = "TweetusDeletus/" + __version__

    def log_message(self, fmt, *args):  # keep the terminal quiet
        return

    # -- plumbing -----------------------------------------------------

    def _allowed_hosts(self) -> set[str]:
        port = self.app.port
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self._allowed_hosts()

    def _token_ok(self) -> bool:
        sent = self.headers.get("X-Tweetus-Token", "")
        return hmac.compare_digest(sent.encode(), self.app.token.encode())

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # same-origin fetches may omit it; the token still guards
        return origin in {"http://" + h for h in self._allowed_hosts()}

    def _send(self, status: int, body: bytes, ctype: str,
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https: data:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_JSON_BODY:
            raise UserError("Request too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise UserError("Bad request body.") from None
        if not isinstance(data, dict):
            raise UserError("Bad request body.")
        return data

    # -- dispatch -----------------------------------------------------

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self):  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._send(403, b"Forbidden", "text/plain")
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if path.startswith("/api/"):
                if not self._token_ok():
                    self._json(403, {"error": "Reload the page and try again."})
                    return
                if method != "GET" and not self._origin_ok():
                    self._json(403, {"error": "Cross-site request refused."})
                    return
                route = API_ROUTES.get((method, path))
                if route is None:
                    self._json(404, {"error": "Not found"})
                    return
                route(self, query)
                return
            if method == "GET":
                if path in ("/", "/index.html"):
                    return self._index()
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if path == "/favicon.ico":
                    return self._static("icon.svg")
                if path == "/login":
                    return self._login()
                if path == "/callback":
                    return self._callback(query)
            self._send(404, b"Not found", "text/plain")
        except (UserError, ValueError, TweetusError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"error": f"Something went wrong: {exc}"})

    # -- pages ----------------------------------------------------------

    def _index(self):
        with open(os.path.join(UI_DIR, "index.html"), "r",
                  encoding="utf-8") as fh:
            html = fh.read().replace("{{TOKEN}}", self.app.token)
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _static(self, name: str):
        if "/" in name or "\\" in name or name.startswith("."):
            self._send(404, b"Not found", "text/plain")
            return
        fp = os.path.join(UI_DIR, name)
        if not os.path.isfile(fp):
            self._send(404, b"Not found", "text/plain")
            return
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",
                                                  "image/svg+xml"):
            ctype += "; charset=utf-8"
        with open(fp, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _login(self):
        if self.app.demo:
            return self._redirect("/")
        try:
            url = self.app.begin_login()
        except (ConfigError, AuthError) as exc:
            return self._redirect("/?error=" + urllib.parse.quote(str(exc)))
        self._redirect(url)

    def _callback(self, query: dict):
        if "error" in query:
            msg = query.get("error_description") or query["error"]
            if query["error"] == "access_denied":
                msg = "You cancelled the sign-in on X."
            return self._redirect("/?error=" + urllib.parse.quote(msg))
        try:
            self.app.complete_login(query.get("code", ""),
                                    query.get("state", ""))
        except (UserError, AuthError, ConfigError) as exc:
            return self._redirect("/?error=" + urllib.parse.quote(str(exc)))
        self._redirect("/?connected=1")

    # -- api: account & setup ---------------------------------------------

    def api_status(self, q):
        self._json(200, self.app.status())

    def api_setup(self, q):
        if self.app.demo:
            raise UserError("Settings can't be changed in demo mode.")
        body = self._body_json()
        cfg = self.app.config()
        client_id = (body.get("client_id") or "").strip()
        if not client_id:
            raise UserError("Paste your Client ID first.")
        changed = client_id != cfg.client_id
        cfg.client_id = client_id
        if "client_secret" in body:
            cfg.client_secret = (body.get("client_secret") or "").strip() or None
        if body.get("min_interval") not in (None, ""):
            try:
                cfg.min_interval = max(0.0, float(body["min_interval"]))
            except (TypeError, ValueError):
                raise UserError("Pause between requests must be a number.") from None
        cfg.save()
        if changed:
            # Tokens belong to the old app; they won't work with a new one.
            TokenStore(cfg.token_path).clear()
        self.app.reset_auth()
        self._json(200, {"ok": True})

    def api_logout(self, q):
        if not self.app.demo:
            try:
                self.app.auth().logout()
            except (ConfigError, AuthError):
                pass
            self.app.reset_auth()
        self._json(200, {"ok": True})

    # -- api: archive -----------------------------------------------------

    def api_archive_upload(self, q):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise UserError("No file received.")
        filename = os.path.basename(
            urllib.parse.unquote(self.headers.get("X-Filename", "archive.zip")))
        os.makedirs(self.app.data.root, exist_ok=True)
        part = self.app.data.archive_path + ".part"
        remaining = length
        with open(part, "wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        if remaining > 0:
            os.remove(part)
            raise UserError("The upload was cut off. Try again.")
        if not zipfile.is_zipfile(part):
            os.remove(part)
            raise UserError("That isn't a .zip file. Upload the archive "
                            "zip exactly as X gave it to you.")
        try:
            summary = _archive_summary(part, filename)
        except (ValueError, KeyError, zipfile.BadZipFile) as exc:
            os.remove(part)
            raise UserError(f"Couldn't read that archive: {exc}") from None
        if not any(summary["counts"].values()):
            os.remove(part)
            raise UserError("That zip doesn't look like an X archive (no "
                            "data/tweets.js or data/like.js inside).")
        os.replace(part, self.app.data.archive_path)
        self.app.data.save_archive_summary(summary)
        self._json(200, {"archive": summary})

    def api_archive_delete(self, q):
        self.app.data.remove_archive()
        self._json(200, {"ok": True})

    # -- api: scan & delete -------------------------------------------------

    def api_scan(self, q):
        body = self._body_json()
        types = [t for t in body.get("types") or [] if t in TYPE_CATEGORY]
        if not types:
            raise UserError("Pick at least one thing to delete.")
        filters = filters_from_dict(body.get("filters") or {})
        source = body.get("source", "live")
        app = self.app
        archive_path = None
        if source == "archive":
            if not os.path.exists(app.data.archive_path):
                raise UserError("Upload your X archive first.")
            archive_path = app.data.archive_path
        needs_live = source != "archive" or any(
            TYPE_CATEGORY[t] not in ArchiveReader.GROUPS for t in types)
        if needs_live and not app.connected():
            raise UserError("Connect your X account first (Settings).")
        keep_ids = app.data.keep_ids()
        done_keys = app.data.done_keys() if archive_path else set()
        check_pinned = app.connected()

        def work(job):
            run_scan(job, app.actions(job) if app.connected() else None,
                     types, filters, archive_path=archive_path,
                     keep_ids=keep_ids, done_keys=done_keys,
                     check_pinned=check_pinned)

        job = app.jobs.start("scan", "Finding matches", work)
        self._json(200, {"job": job.to_dict()})

    def api_scan_results(self, q):
        scan = self.app.jobs.last_scan
        if not scan or scan.id != q.get("id"):
            raise UserError("Those results are gone. Search again.")
        self._json(200, {"items": [item_json(i) for i in scan.items],
                         "stats": scan.stats})

    def api_delete(self, q):
        body = self._body_json()
        app = self.app
        scan = app.jobs.last_scan
        if not scan or scan.id != body.get("scan_id") or scan.state != "done":
            raise UserError("Those results are out of date. Search again.")
        if not app.connected():
            raise UserError("Connect your X account first (Settings).")
        excluded = set(body.get("exclude") or [])
        items = [i for i in scan.items if f"{i.kind}:{i.id}" not in excluded]
        if not items:
            raise UserError("Nothing is selected.")
        def work(job):
            log = ArchiveLog(app.data.history_path)
            try:
                run_delete(job, app.actions(job), items, log)
            finally:
                log.close()

        # Raises if something else is running; nothing below happens then.
        job = app.jobs.start("delete", f"Deleting {len(items):,} items", work,
                             total=len(items))
        if body.get("remember") and excluded:
            app.data.add_keep([{"id": i.id, "kind": i.kind, "text": i.text}
                               for i in scan.items
                               if f"{i.kind}:{i.id}" in excluded])
        # Items being deleted shouldn't be offered again from these results.
        scan.items = [i for i in scan.items if f"{i.kind}:{i.id}" in excluded]
        self._json(200, {"job": job.to_dict()})

    def api_job(self, q):
        job = self.app.jobs.current
        self._json(200, {"job": job.to_dict() if job else None})

    def api_job_cancel(self, q):
        job = self.app.jobs.current
        if job and job.state == "running":
            job.cancel()
        self._json(200, {"ok": True})

    # -- api: history -------------------------------------------------------

    def api_history(self, q):
        try:
            offset = max(0, int(q.get("offset", 0)))
            limit = min(200, max(1, int(q.get("limit", 50))))
        except ValueError:
            raise UserError("Bad page number.") from None
        rows, total = self.app.data.history(offset, limit, q.get("q", ""))
        self._json(200, {"rows": rows, "total": total})

    def api_history_export(self, q):
        rows = self.app.data.all_history()
        stamp = datetime.now().strftime("%Y-%m-%d")
        if q.get("format") == "json":
            body = json.dumps(rows, indent=2, ensure_ascii=False).encode("utf-8")
            ctype, ext = "application/json", "json"
        else:
            fields = ["logged_at", "outcome", "kind", "id", "created_at",
                      "text", "likes", "reposts", "replies", "handle"]
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fields,
                                    extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
            body = buf.getvalue().encode("utf-8-sig")  # Excel-friendly
            ctype, ext = "text/csv; charset=utf-8", "csv"
        self._send(200, body, ctype, {
            "Content-Disposition":
                f'attachment; filename="tweetus-deletus-history-{stamp}.{ext}"'})

    # -- api: keep list -----------------------------------------------------

    def api_keep(self, q):
        self._json(200, {"keep": self.app.data.keep_list()})

    def api_keep_update(self, q):
        body = self._body_json()
        bad = []
        add = []
        for raw in body.get("add") or []:
            found = extract_id(str(raw))
            if found:
                add.append({"id": found, "kind": "", "text": str(raw)[:200]})
            elif str(raw).strip():
                bad.append(str(raw).strip())
        added = self.app.data.add_keep(add) if add else 0
        if body.get("remove"):
            self.app.data.remove_keep([str(x) for x in body["remove"]])
        self._json(200, {"keep": self.app.data.keep_list(), "added": added,
                         "unrecognized": bad})

    # -- api: auto-delete ---------------------------------------------------

    def api_auto(self, q):
        self._json(200, {"auto": self.app._auto_view(self.app.data.auto_rule())})

    def api_auto_save(self, q):
        body = self._body_json()
        rule = {}
        types = [t for t in body.get("types") or [] if t in TYPE_CATEGORY]
        if body.get("enabled") and not types:
            raise UserError("Pick at least one thing to auto-delete.")
        rule["types"] = types
        rule["enabled"] = bool(body.get("enabled"))
        try:
            older = float(body.get("older_than_days"))
            interval = float(body.get("interval_hours"))
        except (TypeError, ValueError):
            raise UserError("Days and hours must be numbers.") from None
        if older < 1:
            raise UserError("Auto-delete needs an age of at least 1 day.")
        if interval < 1:
            raise UserError("Check at most once an hour.")
        rule["older_than_days"] = older
        rule["interval_hours"] = interval
        rule["keep_pinned"] = bool(body.get("keep_pinned", True))
        rule["keep_keywords"] = [w.strip() for w in (body.get("keep_keywords") or [])
                                 if str(w).strip()]
        max_likes = body.get("max_likes")
        if max_likes in (None, ""):
            rule["max_likes"] = None
        else:
            try:
                rule["max_likes"] = max(0, int(max_likes))
            except (TypeError, ValueError):
                raise UserError("Likes must be a whole number.") from None
        was_enabled = self.app.data.auto_rule().get("enabled")
        if rule["enabled"] and not was_enabled:
            rule["last_run"] = None  # first run happens right away
        saved = self.app.data.save_auto_rule(rule)
        self._json(200, {"auto": self.app._auto_view(saved)})

    def api_auto_run(self, q):
        job = self.app.maybe_run_auto(force=True)
        self._json(200, {"job": job.to_dict() if job else None})


API_ROUTES = {
    ("GET", "/api/status"): Handler.api_status,
    ("POST", "/api/setup"): Handler.api_setup,
    ("POST", "/api/logout"): Handler.api_logout,
    ("POST", "/api/archive"): Handler.api_archive_upload,
    ("DELETE", "/api/archive"): Handler.api_archive_delete,
    ("POST", "/api/scan"): Handler.api_scan,
    ("GET", "/api/scan/results"): Handler.api_scan_results,
    ("POST", "/api/delete"): Handler.api_delete,
    ("GET", "/api/job"): Handler.api_job,
    ("POST", "/api/job/cancel"): Handler.api_job_cancel,
    ("GET", "/api/history"): Handler.api_history,
    ("GET", "/api/history/export"): Handler.api_history_export,
    ("GET", "/api/keep"): Handler.api_keep,
    ("POST", "/api/keep"): Handler.api_keep_update,
    ("GET", "/api/auto"): Handler.api_auto,
    ("POST", "/api/auto"): Handler.api_auto_save,
    ("POST", "/api/auto/run"): Handler.api_auto_run,
}


def make_server(app: App, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    app.port = httpd.server_address[1]
    return httpd


def serve(config_path: str = DEFAULT_CONFIG_PATH, *, demo: bool = False,
          port: int | None = None, open_browser: bool = True) -> int:
    app = App(config_path, demo=demo)
    try:
        callback_port = urllib.parse.urlparse(app.config().redirect_uri).port or 8721
    except ConfigError:
        callback_port = 8721
    if port is None:
        port = 8722 if demo else callback_port
    elif not demo and port != callback_port:
        print(f"Note: your X callback URL uses port {callback_port}, so "
              f"signing in to X won't work on port {port}.")
    url = f"http://127.0.0.1:{port}/"
    try:
        httpd = make_server(app, port)
    except OSError:
        print(f"Port {port} is busy — Tweetus Deletus is probably already "
              f"open. Opening {url}")
        if open_browser:
            webbrowser.open(url)
        return 0

    threading.Thread(target=app.scheduler_loop, daemon=True).start()
    print(f"\n  Tweetus Deletus {'(DEMO) ' if demo else ''}is running at {url}")
    print("  Keep this window open while you use it. Press Ctrl+C to quit.\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")
    finally:
        app._stop.set()
        httpd.server_close()
        if demo:
            shutil.rmtree(app.data.root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(serve(demo="--demo" in sys.argv))
