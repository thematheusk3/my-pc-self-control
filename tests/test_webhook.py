import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import Ctx

from sm import alerts, auth, webhook
from sm.alerts import Alerts
from sm.sampler import Context
from sm.server import make_server
from sm.webhook import MAX_ACCEPTED, MAX_FAILS, Webhook, WebhookError, clean


class FakeAlerts:
    def __init__(self):
        self.got = []

    def external(self, **n):
        self.got.append(n)
        return {"seq": len(self.got), **n}


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        for mod, name, val in ((webhook, "FILE", tmp / "webhook.json"), (auth, "DATA", tmp), (auth, "AUTH_FILE", tmp / "auth.json"),
                               (auth, "SESSIONS_FILE", tmp / "sessions.json"), (auth, "ACCESS_FILE", tmp / "access.json"),
                               (alerts, "LOG_FILE", tmp / "alerts-log.json"), (alerts, "CONF_FILE", tmp / "alerts.json")):
            p = mock.patch.object(mod, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tmp


class CleanTest(unittest.TestCase):
    def test_minimum_and_defaults(self):
        n = clean({"title": "  Oi  "})
        self.assertEqual((n["title"], n["level"], n["duration"], n["display"], n["silent"]), ("Oi", "info", 0, "auto", False))

    def test_limits_and_truncation(self):
        n = clean({"title": "x" * 500, "message": "y" * 5000, "duration": 99999})
        self.assertEqual((len(n["title"]), len(n["text"]), n["duration"]), (120, 1000, 300))
        self.assertEqual(clean({"title": "a", "duration": 1})["duration"], 3)
        self.assertEqual(clean({"title": "a", "duration": 0})["duration"], 0)

    def test_rejects_bad_input(self):
        for body in ([], "x", {}, {"title": ""}, {"title": "a", "level": "x"}, {"title": "a", "duration": "muito"},
                     {"title": "a", "url": "javascript:alert(1)"}, {"title": "a", "url": 'https://x"><img'},
                     {"title": "a", "icon": "<script>"}, {"title": "a", "icon": "../x"}, {"title": "a", "display": "x"}):
            with self.assertRaises(WebhookError, msg=body):
                clean(body)

    def test_icon_accepts_name_and_emoji(self):
        self.assertEqual(clean({"title": "a", "icon": "game-controller"})["icon"], "game-controller")
        self.assertEqual(clean({"title": "a", "icon": "🎮"})["icon"], "🎮")


class TokenTest(_Tmp):
    def test_off_until_token_exists(self):
        with self.assertRaises(WebhookError) as c:
            Webhook(FakeAlerts()).authorize("Bearer x", "1.1.1.1")
        self.assertEqual(c.exception.status, 403)

    def test_token_is_not_stored_and_survives_restart(self):
        h = Webhook(FakeAlerts())
        token = h.new_token()
        self.assertNotIn(token, webhook.FILE.read_text())
        self.assertNotIn(token, str(h.status()))
        Webhook(FakeAlerts()).authorize(f"Bearer {token}", "1.1.1.1")

    def test_new_token_invalidates_old_and_revoke_turns_off(self):
        h = Webhook(FakeAlerts())
        old = h.new_token()
        new = h.new_token()
        with self.assertRaises(WebhookError):
            h.authorize(f"Bearer {old}", "1.1.1.1")
        h.authorize(f"Bearer {new}", "1.1.1.1")
        h.revoke()
        self.assertFalse(h.status()["enabled"])
        with self.assertRaises(WebhookError):
            h.authorize(f"Bearer {new}", "1.1.1.1")

    def test_wrong_scheme_or_missing_header(self):
        h = Webhook(FakeAlerts())
        token = h.new_token()
        for header in (None, "", token, f"Basic {token}"):
            with self.assertRaises(WebhookError):
                h.authorize(header, "1.1.1.1")

    def test_failures_lock_only_that_ip_for_a_while(self):
        t = [1000.0]
        h = Webhook(FakeAlerts(), now=lambda: t[0])
        token = h.new_token()
        for _ in range(MAX_FAILS):
            with self.assertRaises(WebhookError):
                h.authorize("Bearer errado", "1.1.1.1")
        with self.assertRaises(WebhookError) as c:
            h.authorize(f"Bearer {token}", "1.1.1.1")     # até com o token certo
        self.assertEqual(c.exception.status, 429)
        h.authorize(f"Bearer {token}", "2.2.2.2")
        t[0] += 61
        h.authorize(f"Bearer {token}", "1.1.1.1")

    def test_accepted_rate_limit_and_counter(self):
        t = [1000.0]
        fake = FakeAlerts()
        h = Webhook(fake, now=lambda: t[0])
        h.new_token()
        for _ in range(MAX_ACCEPTED):
            h.receive({"title": "a"})
        with self.assertRaises(WebhookError) as c:
            h.receive({"title": "a"})
        self.assertEqual(c.exception.status, 429)
        self.assertEqual(len(fake.got), MAX_ACCEPTED)
        t[0] += 61
        h.receive({"title": "a"})
        self.assertEqual(h.status()["count"], MAX_ACCEPTED + 1)


class AlertsExternalTest(_Tmp):
    def test_goes_to_history_ignoring_rules_and_cooldown(self):
        a = Alerts(Ctx())
        with mock.patch.object(Alerts, "_deliver") as deliver:
            a.external("Um", "x", icon="check", source="App")
            a.external("Um", "x", silent=True)
        self.assertEqual([x["title"] for x in a.history], ["Um", "Um"])
        self.assertEqual(a.history[1]["rule"], "webhook")
        self.assertEqual(a.history[1]["source"], "App")
        self.assertEqual(deliver.call_count, 1, "silent não vai para o PC nem para o ntfy")


class HttpTest(_Tmp):
    """Pelo servidor de verdade, com senha ligada: a rota é a única que dispensa a sessão."""

    def setUp(self):
        super().setUp()
        cfg = Ctx(auth_enabled=True, host="127.0.0.1", port=0).config
        ctx = Context(cfg)
        self.fake = FakeAlerts()
        ctx.webhook = self.hook = Webhook(self.fake)
        self.srv = make_server(ctx)
        self.srv.RequestHandlerClass.auth.set_password("senha-de-teste")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def post(self, path, body, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        c.request("POST", path, raw if raw is not None else json.dumps(body),
                  {"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_end_to_end(self):
        token = self.hook.new_token()
        status, data = self.post("/api/webhook/notify", {"title": "Oi", "message": "m"}, {"Authorization": f"Bearer {token}"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.fake.got[0]["title"], "Oi")

    def test_wrong_token_and_bad_body(self):
        token = self.hook.new_token()
        self.assertEqual(self.post("/api/webhook/notify", {"title": "x"}, {"Authorization": "Bearer nao"})[0], 401)
        self.assertEqual(self.post("/api/webhook/notify", None, {"Authorization": f"Bearer {token}"}, raw="{quebrado")[0], 400)
        self.assertEqual(self.fake.got, [])

    def test_token_does_not_open_the_rest_of_the_panel(self):
        token = self.hook.new_token()
        h = {"Authorization": f"Bearer {token}"}
        for path in ("/api/webhook/token", "/api/webhook/revoke", "/api/webhook/test", "/api/mural"):
            self.assertEqual(self.post(path, {}, h)[0], 401, path)
        self.assertTrue(self.hook.status()["enabled"])


if __name__ == "__main__":
    unittest.main()
