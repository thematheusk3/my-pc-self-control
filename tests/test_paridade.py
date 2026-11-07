"""Recursos trazidos da versão Windows (0.4.7): monitor, engines da GPU, processos críticos, finalizar
educado, PID reaproveitado, reinício pendente e integridade do executável."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import tests.helpers  # noqa: F401  (sys.path e registro de ações temporário)
from sm import actions, display, procinspect, updates
from sm.focus import CLOSE_SCRIPT, FocusMonitor
from sm.hw.drm_clients import DrmClientTracker


def _edid(name: str) -> bytes:
    e = bytearray(128)
    d = bytearray(b"\0\0\0\xfc\0" + name.encode()[:13].ljust(13, b"\n"))
    e[72:90] = d     # segundo descritor
    return bytes(e)


class DisplayTest(unittest.TestCase):
    def test_edid_name(self):
        self.assertEqual(display.edid_name(_edid("LG ULTRAGEAR")), "LG ULTRAGEAR")
        self.assertIsNone(display.edid_name(bytes(128)))

    def test_connector(self):
        self.assertEqual(display.connector_label("DP-4", 14), "DisplayPort")
        self.assertEqual(display.connector_label("HDMI-A-1", None), "HDMI")
        self.assertEqual(display.connector_label("eDP-1", None), "Tela embutida")
        self.assertIsNone(display.connector_label("Virtual-1", None))

    def test_outputs_label_primary(self):
        js = {"outputs": [
            {"name": "DP-5", "connected": True, "priority": 1, "type": 14, "scale": 1.25, "modes": [], "currentModeId": "1"},
            {"name": "DP-4", "connected": True, "priority": 2, "type": 14, "scale": 1, "modes": []},
            {"name": "HDMI-A-1", "connected": False}]}
        with mock.patch.object(display, "_kscreen", return_value=js), \
                mock.patch.object(display, "edid_names", return_value={"DP-5": "LG ULTRAWIDE"}):
            outs = display.outputs()
        self.assertEqual([o["name"] for o in outs], ["DP-5", "DP-4"])
        self.assertEqual(outs[0]["label"], "LG ULTRAWIDE (DP-5)")
        self.assertTrue(outs[0]["primary"])
        self.assertFalse(outs[1]["primary"])
        self.assertIsNone(outs[1]["label"])
        self.assertEqual(outs[0]["connector"], "DisplayPort")


class EnginesTest(unittest.TestCase):
    def test_per_device_engines(self):
        t = DrmClientTracker()
        snaps = [
            {10: {("0000:03:00.0", "1"): {"engines": {"gfx": 0, "dec": 0}, "mem": 0, "pdev": "0000:03:00.0"}}},
            {10: {("0000:03:00.0", "1"): {"engines": {"gfx": 500_000_000, "dec": 100_000_000}, "mem": 0,
                                          "pdev": "0000:03:00.0"}}},
        ]
        with mock.patch("sm.hw.drm_clients._scan", side_effect=snaps), \
                mock.patch("sm.hw.drm_clients.time.monotonic", side_effect=[100.0, 101.0]):
            t.update()
            t.update()
        e = t.per_device_engines["0000:03:00.0"]
        self.assertAlmostEqual(e["gfx"], 50.0, delta=0.1)
        self.assertAlmostEqual(e["dec"], 10.0, delta=0.1)
        self.assertAlmostEqual(t.per_device_busy["0000:03:00.0"], 50.0, delta=0.1)


class CriticalAndCloseTest(unittest.TestCase):
    def setUp(self):
        self.child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(self.child.wait)
        self.addCleanup(self.child.kill)
        time.sleep(0.1)

    def test_critical_needs_confirmation(self):
        with mock.patch.dict(actions.CRITICAL, {"python3": "teste", os.path.basename(sys.executable)[:15]: "teste"}):
            with self.assertRaises(actions.ActionError) as cm:
                actions.signal_process(self.child.pid, "TERM", None)
            self.assertEqual(cm.exception.status, 409)
            self.assertTrue(cm.exception.extra["critical"])
            self.assertIsNone(self.child.poll())
            actions.signal_process(self.child.pid, "TERM", None, confirm_critical=True)
        self.assertEqual(self.child.wait(5), -signal.SIGTERM)

    def test_polite_close_with_windows_does_not_signal(self):
        calls = []
        r = actions.signal_process(self.child.pid, "TERM", None, close_windows=lambda pid: calls.append(pid) or 2)
        self.assertEqual(r["closed_windows"], 2)
        self.assertEqual(calls, [self.child.pid])
        time.sleep(0.2)
        self.assertIsNone(self.child.poll())      # só pediu para fechar

    def test_no_window_falls_back_to_sigterm(self):
        r = actions.signal_process(self.child.pid, "TERM", None, close_windows=lambda pid: 0)
        self.assertIn("note", r)
        self.assertEqual(self.child.wait(5), -signal.SIGTERM)

    def test_unknown_result_falls_back_to_sigterm(self):
        actions.signal_process(self.child.pid, "TERM", None, close_windows=lambda pid: None)
        self.assertEqual(self.child.wait(5), -signal.SIGTERM)

    def test_kill_skips_polite_close(self):
        calls = []
        actions.signal_process(self.child.pid, "KILL", None, close_windows=lambda pid: calls.append(pid) or 1)
        self.assertEqual(calls, [])
        self.assertEqual(self.child.wait(5), -signal.SIGKILL)


class KwinCloseTest(unittest.TestCase):
    def test_script_and_reply(self):
        script = CLOSE_SCRIPT % (1234, "abc")
        self.assertIn("w.pid === 1234", script)
        self.assertIn('print("SMCLOSE abc " + n)', script)
        m = FocusMonitor()
        m.script_file = Path(tempfile.mkdtemp()) / "kwin-focus.js"
        m.mode = "kwin"

        def fake_run(path, name):
            token = name.rsplit("-", 1)[1]
            m._close_reply(f"js: SMCLOSE {token} 3\n")
            return True
        with mock.patch.object(m, "_run_script", side_effect=fake_run), mock.patch("sm.focus._busctl"):
            self.assertEqual(m.close_windows(1234), 3)
        self.assertEqual(m._closing, {})
        self.assertEqual(list(m.script_file.parent.iterdir()), [])     # script temporário apagado

    def test_timeout_returns_none(self):
        m = FocusMonitor()
        m.script_file = Path(tempfile.mkdtemp()) / "kwin-focus.js"
        m.mode = "kwin"
        with mock.patch.object(m, "_run_script", return_value=True), mock.patch("sm.focus._busctl"):
            self.assertIsNone(m.close_windows(1234, timeout=0.1))


class ReusedPidTest(unittest.TestCase):
    def test_reused_pid_is_finished(self):
        from sm.proctrack import ProcessTracker
        from tests.helpers import Ctx
        t = ProcessTracker(Ctx())
        t.persist = False
        pid = os.getpid()
        with t._lock:
            t.live[pid] = {"id": f"{pid}-1", "pid": pid, "ppid": 1, "name": "velho", "start": time.time() - 10,
                           "peak_cpu": 0.0, "peak_mem": 0}
        t._n = 1
        t._scan()     # _n = 2: verifica reuso
        self.assertTrue(any(r["name"] == "velho" for r in t.exited))
        self.assertNotEqual(t.live.get(pid, {}).get("name"), "velho")


class RebootTest(unittest.TestCase):
    def _mod(self, root: Path, ver: str, base: str, mtime: float):
        d = root / ver
        d.mkdir()
        (d / "pkgbase").write_text(base + "\n")
        (d / "vmlinuz").write_bytes(b"x")
        os.utime(d / "vmlinuz", (mtime, mtime))

    def test_kernel(self):
        root = Path(tempfile.mkdtemp())
        self._mod(root, "7.2.11-arch1-1", "linux", 1000)
        self._mod(root, "6.18.57-2-lts", "linux-lts", 500)
        with mock.patch.object(updates, "REBOOT_FLAGS", ()), mock.patch("shutil.which", return_value=None):
            self.assertIsNone(updates.reboot_required("7.2.11-arch1-1", root))
            self.assertIsNone(updates.reboot_required("6.18.57-2-lts", root))
            # rodando um kernel cuja pasta já foi apagada pelo pacote novo
            r = updates.reboot_required("7.2.10-arch1-1", root)
            self.assertEqual(r["reason"], "kernel")
            # cópia antiga guardada por hook (mais velha) não conta; versão nova instalada depois conta
            self._mod(root, "7.2.10-arch1-1", "linux", 900)
            self.assertIsNone(updates.reboot_required("7.2.11-arch1-1", root))
            self.assertEqual(updates.reboot_required("7.2.10-arch1-1", root)["installed"], "7.2.11-arch1-1")


class PackageIntegrityTest(unittest.TestCase):
    def test_verify(self):
        f = Path(tempfile.mkdtemp()) / "prog"
        f.write_bytes(b"conteudo")
        sha, _ = procinspect.sha256_of(str(f))
        with mock.patch.object(procinspect.PKG, "tool", "pacman"), \
                mock.patch.object(procinspect.PKG, "expected_digest", return_value=("sha256", sha)):
            self.assertEqual(procinspect.verify_package_file("prog", str(f), sha), "ok")
        with mock.patch.object(procinspect.PKG, "tool", "pacman"), \
                mock.patch.object(procinspect.PKG, "expected_digest", return_value=("sha256", "0" * 64)):
            self.assertEqual(procinspect.verify_package_file("prog", str(f), sha), "changed")
        import hashlib
        with mock.patch.object(procinspect.PKG, "tool", "dpkg-query"), \
                mock.patch.object(procinspect.PKG, "expected_digest",
                                  return_value=("md5", hashlib.md5(b"conteudo").hexdigest())):
            self.assertEqual(procinspect.verify_package_file("prog", str(f), None), "ok")
        self.assertIsNone(procinspect.verify_package_file(None, str(f), sha))

    def test_flag(self):
        fl = procinspect.flags_for({"exe": "/usr/bin/x", "exe_verified": True, "name": "x", "cmd": "x"}, "pkg",
                                   {"package_check": "changed"})
        self.assertTrue(any(x["level"] == "alto" and "pacote pkg" in x["text"] for x in fl))

    def test_mtree_unescape_and_glob(self):
        self.assertEqual(procinspect._mtree_unescape(r"./usr/bin/a\040b"), "./usr/bin/a b")
        self.assertEqual(procinspect.glob_escape("gtk[3]"), "gtk[[]3]")


if __name__ == "__main__":
    unittest.main()
