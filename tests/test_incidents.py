"""Investigador de incidentes, regras do journal, banco persistente, serviços, logs, inventário e frota."""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from sm import fleet, hwinfo, incidents, journal, rules, services
from sm.history import History
from sm.store import Store


def jentry(msg, ts, kernel=False, prio=3, mono=10_000_000_000, **extra):
    e = {"MESSAGE": msg, "__REALTIME_TIMESTAMP": str(int(ts * 1e6)), "__MONOTONIC_TIMESTAMP": str(mono),
         "PRIORITY": str(prio), "_BOOT_ID": "b" * 32, "__CURSOR": f"s=x;i={int(ts * 1000)}"}
    if kernel:
        e.update({"_TRANSPORT": "kernel", "SYSLOG_IDENTIFIER": "kernel"})
    e.update(extra)
    return e


def coredump(ts, comm="eldenring.exe", exe="/x/Proton 9.0/files/bin/wine64-preloader", signal="SIGSEGV"):
    return jentry(f"Process 5613 ({comm}) of user 1000 dumped core.", ts, prio=2, MESSAGE_ID=rules.COREDUMP_ID,
                  SYSLOG_IDENTIFIER="systemd-coredump", COREDUMP_EXE=exe, COREDUMP_COMM=comm, COREDUMP_PID="5613",
                  COREDUMP_SIGNAL_NAME=signal, COREDUMP_USER_UNIT="app-steam-app1245620-1.scope")


class RulesTest(unittest.TestCase):
    def test_gpu_hang_is_trigger(self):
        ev = rules.classify(jentry("amdgpu 0000:01:00.0: amdgpu: ring gfx_0.0.0 timeout, signaled seq=1", 100, kernel=True))
        self.assertEqual((ev["kind"], ev["family"], ev["trigger"]), ("gpu_hang", "gpu", True))

    def test_default_is_not_a_fault(self):
        ev = rules.classify(jentry("amdgpu 0000:01:00.0: Trusted Memory Zone (TMZ) feature disabled as experimental (default)",
                                   100, kernel=True, prio=6))
        self.assertIsNone(ev)

    def test_fence_fallback_is_only_a_warning(self):
        ev = rules.classify(jentry("amdgpu 0000:01:00.0: Fence fallback timer expired on ring sdma0", 100, kernel=True, prio=4))
        self.assertEqual((ev["kind"], ev["trigger"]), ("gpu_fence", False))

    def test_coredump_fields(self):
        ev = rules.classify(coredump(100))
        self.assertEqual(ev["kind"], "coredump")
        self.assertEqual(ev["data"]["signal"], "SIGSEGV")
        self.assertEqual(ev["unit"], "app-steam-app1245620-1.scope")
        self.assertEqual(ev["pid"], 5613)

    def test_unit_failed_and_ignored(self):
        ev = rules.classify(jentry("foo.service: Failed with result 'exit-code'.", 100, prio=4, SYSLOG_IDENTIFIER="systemd",
                                   UNIT="foo.service"))
        self.assertEqual((ev["kind"], ev["unit"], ev["data"]["result"], ev["trigger"]), ("unit_failed", "foo.service", "exit-code", True))
        self.assertIsNone(rules.classify(jentry("systemd-coredump@1.service: Failed with result 'x'.", 100, prio=4,
                                                SYSLOG_IDENTIFIER="systemd", UNIT="systemd-coredump@1.service")))

    def test_boot_noise_does_not_trigger(self):
        ev = rules.classify(jentry("amdgpu: ring gfx timeout", 100, kernel=True, mono=30_000_000))
        self.assertFalse(ev["trigger"])
        disk = rules.classify(jentry("nvme0n1: I/O error, dev nvme0n1, sector 12", 100, kernel=True, mono=30_000_000))
        self.assertTrue(disk["trigger"])        # disco/hardware abre mesmo no boot

    def test_generic_error(self):
        ev = rules.classify(jentry("algo deu errado", 100, prio=3, SYSLOG_IDENTIFIER="app"))
        self.assertEqual((ev["kind"], ev["trigger"]), ("error", False))
        self.assertIsNone(rules.classify(jentry("tudo bem", 100, prio=6, SYSLOG_IDENTIFIER="app")))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.st = Store("m1", Path(tempfile.mkdtemp()) / "s.db")

    def tearDown(self):
        self.st.close()

    def test_event_merge(self):
        ev = {"ts": 100.0, "kind": "error", "family": "other", "severity": "error", "message": "x", "unit": "a.service"}
        a = self.st.add_event(ev)
        b = self.st.add_event({**ev, "ts": 150.0})
        c = self.st.add_event({**ev, "ts": 1000.0})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(self.st.events(since=0)[1]["count"], 2)

    def test_incident_crud_and_changes(self):
        iid = self.st.add_incident({"uid": "u1", "trigger_ts": 10, "family": "gpu", "kind": "gpu_hang", "severity": "critical",
                                    "title": "t"})
        self.st.update_incident(iid, status="resolved", report={"a": 1}, note="ok")
        inc = self.st.incident(iid)
        self.assertEqual((inc["status"], inc["report"], inc["note"]), ("resolved", {"a": 1}, "ok"))
        ch = self.st.changes(0)
        self.assertEqual(len(ch["incidents"]), 1)
        self.assertEqual(self.st.changes(ch["next"])["incidents"], [])
        self.assertTrue(self.st.delete_incident(iid))
        self.assertIsNone(self.st.incident(iid))

    def test_metrics_rollup(self):
        self.st.add_metrics(60, 3600, {"cpu.temp": (50, 40, 60, 60)})
        self.st.add_metrics(60, 3660, {"cpu.temp": (70, 65, 90, 60)})
        self.st.rollup_hour(3600)
        h = self.st.metrics(["cpu.temp"], 0, 10000, res=3600)["cpu.temp"][0]
        self.assertEqual(h, [3600, 60.0, 40.0, 90.0])


class InvestigatorTest(unittest.TestCase):
    def setUp(self):
        self.st = Store("m1", Path(tempfile.mkdtemp()) / "s.db")
        self.orig = (journal.window, journal.entry, journal.boots, hwinfo.recent_upgrades)
        journal.window = lambda *a, **k: []
        journal.entry = lambda cur: None
        journal.boots = lambda limit=3: []
        hwinfo.recent_upgrades = lambda days=7, now=None: []
        self.alerts = []
        ctx = SimpleNamespace(history=History(300), processes=[{"pid": 1, "name": "jogo", "cpu": 90, "mem": 10}], data={},
                              alerts=SimpleNamespace(event=lambda *a, **k: self.alerts.append(a)), proctrack=None)
        self.inv = incidents.Investigator(ctx, self.st)

    def tearDown(self):
        journal.window, journal.entry, journal.boots, hwinfo.recent_upgrades = self.orig
        time.sleep(0.05)
        self.st.close()

    def feed(self, e):
        ev = rules.classify(e)
        self.inv.on_event(ev, self.st.add_event(ev))
        return ev

    def build_all(self):
        for iid, p in list(self.inv.pending.items()):
            self.inv.pending.pop(iid)
            self.inv.build(iid, p)

    def test_gpu_hang_then_crash_is_one_incident(self):
        t = time.time() - 5
        self.feed(jentry("amdgpu: ring gfx_0.0.0 timeout", t, kernel=True))
        self.feed(jentry("amdgpu: GPU reset begin!", t + 1, kernel=True))
        self.feed(coredump(t + 3))
        self.assertEqual(len(self.inv.pending), 1)
        self.build_all()
        inc = self.st.incidents()[0]
        self.assertEqual(inc["kind"], "gpu_reset")
        self.assertIn("GPU travou e", inc["title"])
        top = self.st.incident(inc["id"])["report"]["hypotheses"][0]
        self.assertEqual(top["id"], "gpu")
        self.assertEqual(top["level"], "alta")
        self.assertTrue(self.alerts)                         # incidente ao vivo avisa
        self.assertIn("## Hipóteses", self.st.incident(inc["id"])["report"]["ai_text"])

    def test_repeat_counts_on_same_incident(self):
        t = time.time() - 600
        self.feed(coredump(t))
        self.build_all()
        self.feed(coredump(t + 300))
        incs = self.st.incidents()
        self.assertEqual(len(incs), 1)
        self.assertEqual(incs[0]["count"], 2)
        self.assertEqual(incs[0]["subject"], "exe:eldenring.exe")      # wine64-preloader não serve de nome

    def test_oom_is_memory(self):
        self.feed(jentry("Out of memory: Killed process 1234 (firefox)", time.time() - 100, kernel=True))
        self.build_all()
        r = self.st.incident(self.st.incidents()[0]["id"])["report"]
        self.assertEqual(r["hypotheses"][0]["id"], "memory")

    def test_unexpected_shutdown(self):
        journal.boots = lambda limit=3: [{"index": -1, "id": "a" * 32, "first": 1, "last": 1000}]
        orig = journal.last_of_boot
        journal.last_of_boot = lambda b, n=300: [jentry("algo normal", 990, prio=6, SYSLOG_IDENTIFIER="x")]
        try:
            iid = self.inv.check_previous_boot()
            self.assertIsNotNone(iid)
            self.assertEqual(self.st.incident(iid)["kind"], "unexpected_shutdown")
            self.assertIsNone(self.inv.check_previous_boot())          # o mesmo boot não é verificado de novo
            journal.boots = lambda limit=3: [{"index": -1, "id": "c" * 32, "first": 1, "last": 1000}]
            journal.last_of_boot = lambda b, n=300: [jentry("Stopping Flush Journal to Persistent Storage...", 990, prio=6)]
            self.assertIsNone(self.inv.check_previous_boot())          # desligou normalmente
        finally:
            journal.last_of_boot = orig

    def test_manual_investigation(self):
        iid = self.inv.investigate(time.time() - 30, note="tela congelou")
        for _ in range(50):
            if self.st.incident(iid)["status"] != "analyzing":
                break
            time.sleep(0.05)
        inc = self.st.incident(iid)
        self.assertEqual(inc["status"], "open")
        self.assertEqual(inc["report"]["note"], "tela congelou")


class MiscTest(unittest.TestCase):
    def test_parse_show(self):
        out = services.parse_show("Id=a.service\nMemoryCurrent=10\n\nId=b.service\nMemoryCurrent=[not set]\n")
        self.assertEqual([d["Id"] for d in out], ["a.service", "b.service"])
        self.assertIsNone(services._int("18446744073709551615"))

    def test_service_validation(self):
        for bad in ("a;rm -rf", "../x.service", "x"):
            with self.assertRaises(ValueError):
                services.action(bad, "restart")
        with self.assertRaises(ValueError):
            services.action("a.service", "mask")
        with self.assertRaises(PermissionError):
            services.action("self-monitoring.service", "stop")

    def test_logs_validation(self):
        for q in ({"unit": "a b"}, {"boot": "x; ls"}, {"before": "a"}, {"ident": "a b"}):
            with self.assertRaises(ValueError):
                journal.query(q)

    def test_hw_diff(self):
        d = hwinfo.diff({"kernel": "6.1", "gpu": {"vbios": "a"}}, {"kernel": "6.2", "gpu": {"vbios": "a"}})
        self.assertEqual(d, [{"path": "kernel", "old": "6.1", "new": "6.2"}])
        self.assertEqual(hwinfo.diff(None, {"a": 1}), [])

    def test_machine_id(self):
        m = fleet.machine_id()
        self.assertRegex(m, r"^[0-9a-f]{16}$")
        self.assertEqual(m, fleet.machine_id())

    def test_route_order(self):
        from sm import api
        from sm.api import routes  # noqa: F401
        fn, _ = api.match("POST", "/api/incidents/investigate")
        self.assertEqual(fn.__name__, "incident_investigate")
        fn, p = api.match("POST", "/api/incidents/12")
        self.assertEqual((fn.__name__, p), ("incident_update", {"iid": "12"}))


if __name__ == "__main__":
    unittest.main()
