"""Quem está acessando: sessões com última atividade, histórico de acessos, aviso no PC a cada login."""
from __future__ import annotations

import http.client
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import Ctx

from sm import auth, clients
from sm.sampler import Context
from sm.server import COOKIE, make_server


class _TmpData(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        for name, val in (("DATA", tmp), ("AUTH_FILE", tmp / "auth.json"), ("SESSIONS_FILE", tmp / "sessions.json"),
                          ("ACCESS_FILE", tmp / "access-log.json")):
            p = mock.patch.object(auth, name, val)
            p.start()
            self.addCleanup(p.stop)


class AuthSessionsTest(_TmpData):
    def test_list_seen_and_end(self):
        a = auth.Auth()
        me = a.create_session("192.168.1.10", "Mozilla/5.0 (Windows NT 10.0) Chrome/130")
        other = a.create_session("192.168.1.23", "Mozilla/5.0 (Linux; Android 15) Chrome/130 Mobile")
        self.assertTrue(a.valid(other, "192.168.1.24"))
        items = a.list_sessions(me)
        self.assertEqual([x["current"] for x in items], [True, False])
        self.assertEqual(items[1]["ip"], "192.168.1.23")
        self.assertEqual(items[1]["last_ip"], "192.168.1.24")
        self.assertNotIn(other, str(items))                      # o cookie nunca sai na API
        self.assertIsNotNone(a.end_by_id(items[1]["id"]))
        self.assertFalse(a.valid(other))
        self.assertIsNone(a.end_by_id("nao-existe"))
        self.assertEqual(len(auth.Auth().list_sessions(me)), 1)  # persistido

    def test_access_log_persisted_newest_first(self):
        a = auth.Auth()
        a.record("fail", "10.0.0.5", "x")
        a.record("login", "10.0.0.5", "x")
        self.assertEqual([e["event"] for e in auth.Auth().access_log()], ["login", "fail"])


class ClientsTest(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(clients.kind("127.0.0.1"), "pc")
        self.assertEqual(clients.kind("::ffff:127.0.0.1"), "pc")
        self.assertEqual(clients.kind("10.250.250.250"), "lan")
        self.assertEqual(clients.kind("100.101.102.103"), "vpn")
        self.assertEqual(clients.kind("fe80::1"), "link-local")
        self.assertEqual(clients.kind("8.8.8.8"), "internet")
        self.assertEqual(clients.kind("lixo"), "?")

    def test_device_and_mac(self):
        self.assertEqual(clients.device("Mozilla/5.0 (Linux; Android 15; SM-S921B) AppleWebKit Chrome/130 Mobile Safari"),
                         "Android · Chrome")
        self.assertEqual(clients.device("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Version/18.0 Mobile Safari/604.1"),
                         "iPhone · Safari")
        self.assertTrue(clients.mac_random("da:a1:19:00:00:01"))
        self.assertFalse(clients.mac_random("00:1a:2b:3c:4d:5e"))

    def test_arp_table_runs(self):
        self.assertIsInstance(clients.arp_table(), dict)


class LoginAnnounceTest(_TmpData):
    def setUp(self):
        super().setUp()
        self.notified = []
        done = threading.Event()

        def fake_notify(title, body, **kw):
            self.notified.append((title, body))
            done.set()
            return True

        self.done = done
        for target, val in (("sm.desktop.notify_pc", fake_notify), ("sm.server.time.sleep", lambda s: None),
                            ("sm.clients.hostname", lambda ip, wait=0: "celular-teste")):
            p = mock.patch(target, val)
            p.start()
            self.addCleanup(p.stop)
        cfg = Ctx(auth_enabled=True, host="127.0.0.1", port=0).config
        self.srv = make_server(Context(cfg))
        self.srv.RequestHandlerClass.auth.set_password("segredo123")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body=None, cookie=None, ctype="application/x-www-form-urlencoded"):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        h = {"User-Agent": "Mozilla/5.0 (Linux; Android 15) Chrome/130 Mobile", "Content-Type": ctype}
        if cookie:
            h["Cookie"] = f"{COOKIE}={cookie}"
        c.request(method, path, body, h)
        r = c.getresponse()
        r.read()
        return r

    def test_login_notifies_pc_and_lists_session(self):
        r = self.req("POST", "/login", "password=segredo123")
        sid = r.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]
        self.assertTrue(self.done.wait(5))
        title, body = self.notified[0]
        self.assertIn("Novo acesso", title)
        self.assertIn("Android · Chrome", body)
        self.assertIn("127.0.0.1", body)

        import json
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        c.request("GET", "/api/auth/sessions", headers={"Cookie": f"{COOKIE}={sid}"})
        data = json.loads(c.getresponse().read())
        self.assertTrue(data["sessions"][0]["current"])
        self.assertEqual(data["sessions"][0]["where"]["kind"], "pc")
        self.assertEqual(data["log"][0]["event"], "login")

        r = self.req("POST", "/api/auth/sessions/end", json.dumps({"id": data["sessions"][0]["id"]}), sid, "application/json")
        self.assertEqual(r.status, 400)                          # a própria sessão: usar "Sair"

    def test_lockout_notifies_pc(self):
        for _ in range(auth.MAX_FAILS):
            self.req("POST", "/login", "password=errada")
        self.assertTrue(self.done.wait(5))
        self.assertIn("Senha errada", self.notified[0][0])
        events = [e["event"] for e in self.srv.RequestHandlerClass.auth.access_log()]
        self.assertEqual(events[0], "lockout")
        self.assertEqual(events.count("fail"), auth.MAX_FAILS - 1)


if __name__ == "__main__":
    unittest.main()
