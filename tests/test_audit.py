"""Registro de ações feitas pelo painel."""
from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import Ctx

from sm import audit
from sm.sampler import Context
from sm.server import make_server

WHO = {"ip": "192.168.1.23", "device": "Android · Chrome", "session": "abc"}


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        for name, val in (("FILE", tmp / "audit-log.json"), ("SAVE_DELAY", 0.01)):
            p = mock.patch.object(audit, name, val)
            p.start()
            self.addCleanup(p.stop)


class AuditTest(_Tmp):
    def test_merge_slider_and_persist(self):
        a = audit.Audit()
        for v in (10, 20, 35):
            a.record("audio", f"Volume da saída Fone: {v}%", WHO, True, merge="audio-vol:sink:fone")
        a.record("audio", "Volume da saída Fone: 40%", {**WHO, "session": "outra"}, True, merge="audio-vol:sink:fone")
        a.record("process", "Finalizar x.exe (PID 1)", WHO, False, "Processo não existe mais.")
        items = a.list()
        self.assertEqual([e["text"] for e in items],
                         ["Finalizar x.exe (PID 1)", "Volume da saída Fone: 40%", "Volume da saída Fone: 35%"])
        self.assertEqual(items[2]["count"], 3)
        self.assertNotIn("merge", items[0])
        self.assertEqual([e["cat"] for e in a.list(cat="audio")], ["audio", "audio"])
        self.assertEqual(len(a.list(session="outra")), 1)
        self.assertEqual(a.list(before=items[1]["seq"])[0]["text"], "Volume da saída Fone: 35%")
        a.flush()
        self.assertEqual(len(audit.Audit().list()), 3)

    def test_descriptions(self):
        cat, text, merge = audit.describe("kill", None, {"signal": "KILL"}, {"pid": str(os.getpid())})
        self.assertEqual(cat, "process")
        self.assertRegex(text, r"^Forçar finalização de python.*\(PID \d+\)$")
        self.assertEqual(audit.describe("power_timer_set", None, {"action": "reboot", "minutes": 30}, {})[1],
                         "Agendar para reiniciar em 30 min")
        self.assertEqual(audit.describe("media_control", None, {"op": "seek", "offset": -10, "player": "Spotify.exe"}, {})[1],
                         "Voltar 10 s (Spotify.exe)")
        self.assertIsNone(audit.describe("snapshot", None, {}, {}))     # leitura não é registrada


class DispatchTest(_Tmp):
    def setUp(self):
        super().setUp()
        cfg = Ctx(auth_enabled=False, host="127.0.0.1", port=0, allow_control=False, allow_kill=True).config
        self.srv = make_server(Context(cfg))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def post(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        c.request("POST", path, json.dumps(body), {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (iPhone)"})
        r = c.getresponse()
        r.read()
        return r.status

    def test_post_routes_are_recorded_with_result(self):
        self.assertEqual(self.post("/api/display/dpms", {"on": False}), 403)
        self.assertEqual(self.post("/api/processes/999999/signal", {"signal": "TERM"}), 404)
        items = self.srv.RequestHandlerClass.audit.list()
        self.assertEqual([e["cat"] for e in items], ["process", "display"])
        self.assertTrue(all(not e["ok"] for e in items))
        self.assertIn("desativado", items[1]["error"])
        self.assertEqual(items[1]["text"], "Apagar a imagem dos monitores")
        self.assertEqual(items[0]["device"], "iPhone · navegador")
        self.assertEqual(items[0]["ip"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
