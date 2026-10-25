"""Web UI server tests: a real server on a random port, no network to X.

Run: python3 -m unittest discover -s tests   (or python3 tests/test_webui.py)
"""

import http.client
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tweetus_deletus.webui import App, make_server  # noqa: E402


def fake_archive_bytes() -> bytes:
    tweets = "window.YTD.tweets.part0 = [" + ",".join(
        '{"tweet": {"id_str": "%d", "full_text": "old post %d", '
        '"created_at": "Wed Oct 10 20:19:24 +0000 2018", "favorite_count": "1"}}'
        % (500 + i, i) for i in range(5)) + "]"
    acct = ('window.YTD.account.part0 = [{"account": {"username": "you_demo", '
            '"accountId": "1000000001"}}]')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data/tweets.js", tweets)
        z.writestr("data/account.js", acct)
    return buf.getvalue()


class ServerCase(unittest.TestCase):
    demo = True

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = App(os.path.join(self.tmp, "config.toml"), demo=self.demo)
        self.httpd = make_server(self.app, 0)
        self.port = self.app.port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method, path, body=None, *, token=True, host=None,
                headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            hdrs["X-Tweetus-Token"] = self.app.token
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        ctype = resp.getheader("Content-Type", "")
        parsed = json.loads(payload) if "json" in ctype else payload
        return resp.status, parsed, resp

    def wait_job(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, data, _ = self.request("GET", "/api/job")
            if data["job"] and data["job"]["state"] != "running":
                return data["job"]
            time.sleep(0.05)
        self.fail("job didn't finish")


class SecurityTests(ServerCase):
    def test_api_needs_token(self):
        status, _, _ = self.request("GET", "/api/status", token=False)
        self.assertEqual(status, 403)

    def test_wrong_host_refused(self):
        status, _, _ = self.request("GET", "/api/status",
                                    host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)

    def test_cross_origin_post_refused(self):
        status, data, _ = self.request("POST", "/api/job/cancel", {},
                                       headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_page_has_token_and_no_framing(self):
        status, body, resp = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.app.token.encode(), body)
        self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'",
                      resp.getheader("Content-Security-Policy"))

    def test_static_traversal_blocked(self):
        for path in ("/static/../webui.py", "/static/..%2Fwebui.py",
                     "/static/.hidden"):
            status, _, _ = self.request("GET", path, token=False)
            self.assertEqual(status, 404, path)


class DemoFlowTests(ServerCase):
    def test_scan_delete_history_keep(self):
        status, data, _ = self.request("POST", "/api/scan", {
            "types": ["posts", "replies"], "source": "live",
            "filters": {"older_than_days": 365, "keep_pinned": True}})
        self.assertEqual(status, 200, data)
        job = self.wait_job()
        self.assertEqual(job["state"], "done", job)
        status, res, _ = self.request("GET", f"/api/scan/results?id={job['id']}")
        items = res["items"]
        self.assertGreater(len(items), 10)
        self.assertTrue({i["kind"] for i in items} <= {"post", "reply", "quote"})
        pinned = self.app._demo.pinned_tweet_id()
        self.assertNotIn(pinned, {i["id"] for i in items})

        keep = items[:2]
        exclude = [f"{i['kind']}:{i['id']}" for i in keep]
        status, data, _ = self.request("POST", "/api/delete", {
            "scan_id": job["id"], "exclude": exclude, "remember": True})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["job"]["total"], len(items) - 2)  # known up front
        done = self.wait_job(60)
        self.assertEqual(done["state"], "done", done)
        self.assertEqual(done["stats"]["deleted"], len(items) - 2)

        _, hist, _ = self.request("GET", "/api/history?limit=5")
        self.assertEqual(hist["total"], len(items) - 2)
        self.assertEqual(len(hist["rows"]), 5)
        _, kept, _ = self.request("GET", "/api/keep")
        self.assertEqual({k["id"] for k in kept["keep"]}, {i["id"] for i in keep})

        # the same results can't be deleted twice
        status, data, _ = self.request("POST", "/api/delete", {
            "scan_id": job["id"], "exclude": exclude})
        self.assertEqual(status, 400)

        status, csv_body, resp = self.request("GET", "/api/history/export?format=csv")
        self.assertEqual(status, 200)
        self.assertIn("attachment", resp.getheader("Content-Disposition"))
        self.assertEqual(csv_body.decode("utf-8-sig").count("\n"), len(items) - 1)

    def test_busy_delete_keeps_results(self):
        self.request("POST", "/api/scan", {"types": ["lists"], "filters": {}})
        job = self.wait_job()
        n = job["stats"]["matched"]
        self.assertGreater(n, 0)
        # Something else (e.g. auto-delete) is running...
        gate = threading.Event()
        self.app.jobs.start("auto", "blocker", lambda j: gate.wait(5))
        status, data, _ = self.request("POST", "/api/delete",
                                       {"scan_id": job["id"], "exclude": []})
        self.assertEqual(status, 400)
        self.assertIn("already running", data["error"])
        gate.set()
        self.wait_job()
        # ...so the results must still be there to try again.
        self.assertEqual(len(self.app.jobs.last_scan.items), n)
        status, data, _ = self.request("POST", "/api/delete",
                                       {"scan_id": job["id"], "exclude": []})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.wait_job()["stats"]["deleted"], n)

    def test_scan_needs_a_type_and_valid_filters(self):
        status, data, _ = self.request("POST", "/api/scan",
                                       {"types": [], "filters": {}})
        self.assertEqual(status, 400)
        status, data, _ = self.request("POST", "/api/scan", {
            "types": ["posts"], "filters": {"regex": "("}})
        self.assertEqual(status, 400)
        self.assertIn("pattern", data["error"])

    def test_archive_upload_and_archive_scan(self):
        status, data, _ = self.request("POST", "/api/archive", raw=b"not a zip",
                                       headers={"X-Filename": "x.zip"})
        self.assertEqual(status, 400)
        status, data, _ = self.request("POST", "/api/archive",
                                       raw=fake_archive_bytes(),
                                       headers={"X-Filename": "twitter.zip"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["archive"]["counts"]["posts"], 5)
        self.assertEqual(data["archive"]["account"]["username"], "you_demo")

        status, data, _ = self.request("POST", "/api/scan", {
            "types": ["posts"], "source": "archive", "filters": {}})
        self.assertEqual(status, 200, data)
        job = self.wait_job()
        self.assertEqual(job["stats"]["sources"], {"posts": "archive"})
        self.assertEqual(job["stats"]["matched"], 5)

    def test_auto_rule_validation_and_run(self):
        status, data, _ = self.request("POST", "/api/auto", {
            "enabled": True, "types": ["posts"], "older_than_days": 0,
            "interval_hours": 6})
        self.assertEqual(status, 400)
        status, data, _ = self.request("POST", "/api/auto", {
            "enabled": True, "types": ["posts"], "older_than_days": 30,
            "interval_hours": 6, "max_likes": "100"})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["auto"]["enabled"])
        status, data, _ = self.request("POST", "/api/auto/run", {})
        self.assertEqual(status, 200, data)
        job = self.wait_job(60)
        self.assertEqual(job["kind"], "auto")
        self.assertEqual(job["state"], "done", job)
        result = self.app.data.auto_rule()["last_result"]
        self.assertTrue(result["ok"])

    def test_settings_locked_in_demo(self):
        status, data, _ = self.request("POST", "/api/setup", {"client_id": "x"})
        self.assertEqual(status, 400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "config.toml")))


class RealModeTests(ServerCase):
    demo = False

    def test_setup_then_login_redirect(self):
        _, st, _ = self.request("GET", "/api/status")
        self.assertFalse(st["configured"])
        self.assertFalse(st["connected"])

        status, data, _ = self.request("POST", "/api/setup", {"client_id": "abc123"})
        self.assertEqual(status, 200, data)
        cfg = os.path.join(self.tmp, "config.toml")
        self.assertEqual(os.stat(cfg).st_mode & 0o777, 0o600)

        status, _, resp = self.request("GET", "/login", token=False)
        self.assertEqual(status, 302)
        url = urllib.parse.urlparse(resp.getheader("Location"))
        q = dict(urllib.parse.parse_qsl(url.query))
        self.assertEqual(url.netloc, "x.com")
        self.assertEqual(q["client_id"], "abc123")
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertIn("offline.access", q["scope"])

    def test_bundled_client_id_skips_setup(self):
        from tweetus_deletus import config
        bundled = os.path.join(self.tmp, "bundled.json")
        with open(bundled, "w") as fh:
            json.dump({"client_id": "BUILT_IN_ID"}, fh)
        original = config.BUNDLED_PATH
        config.BUNDLED_PATH = bundled
        try:
            _, st, _ = self.request("GET", "/api/status")
            self.assertTrue(st["configured"])
            self.assertTrue(st["bundled_client"])
            status, _, resp = self.request("GET", "/login", token=False)
            self.assertIn("client_id=BUILT_IN_ID", resp.getheader("Location"))
            # Saving another setting mustn't copy the built-in id into the
            # config file, or a later build with a new id couldn't take over.
            status, data, _ = self.request("POST", "/api/setup", {
                "client_id": "BUILT_IN_ID", "min_interval": "1"})
            self.assertEqual(status, 200, data)
            with open(os.path.join(self.tmp, "config.toml")) as fh:
                self.assertNotIn("client_id", fh.read())
            _, st, _ = self.request("GET", "/api/status")
            self.assertTrue(st["bundled_client"])
            self.assertEqual(st["min_interval"], 1.0)
        finally:
            config.BUNDLED_PATH = original

    def test_quit_stops_the_server(self):
        status, data, _ = self.request("POST", "/api/quit", {})
        self.assertEqual(status, 200)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                self.request("GET", "/api/status")
            except OSError:
                return  # refused: the server is gone
            time.sleep(0.1)
        self.fail("server still answering after quit")

    def test_forged_callback_is_refused(self):
        self.request("POST", "/api/setup", {"client_id": "abc123"})
        status, _, resp = self.request(
            "GET", "/callback?code=x&state=forged", token=False)
        self.assertEqual(status, 302)
        self.assertIn("/?error=", resp.getheader("Location"))

    def test_scan_requires_connection(self):
        self.request("POST", "/api/setup", {"client_id": "abc123"})
        status, data, _ = self.request("POST", "/api/scan",
                                       {"types": ["posts"], "filters": {}})
        self.assertEqual(status, 400)
        self.assertIn("Connect", data["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
