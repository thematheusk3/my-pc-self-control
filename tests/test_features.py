"""Timer de energia, troca de resolução com reversão, alertas, FPS, áudio e atualizações (tudo simulado)."""
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import Ctx

from sm import alerts as al
from sm import audio, display, game, power, updates


class PowerTimerTest(unittest.TestCase):
    def setUp(self):
        self.calls, self.notes = [], []
        self._orig = (power.request, power._can, power.PowerTimer._notify)
        power.request = lambda action, force: self.calls.append((action, force))
        power._can = lambda a: "yes"
        power.PowerTimer._notify = staticmethod(lambda t, b, urgency="normal": self.notes.append(t))
        self.t = power.PowerTimer()
        self.t.file = Path(tempfile.mkdtemp()) / "timer.json"

    def tearDown(self):
        power.request, power._can, power.PowerTimer._notify = self._orig

    def test_schedule_cancel(self):
        st = self.t.schedule("poweroff", 30)
        self.assertAlmostEqual(st["seconds_left"], 1800, delta=2)
        self.assertTrue(self.t.file.exists())
        self.t.cancel()
        self.assertIsNone(self.t.status())
        self.assertFalse(self.t.file.exists())
        self.assertEqual(self.calls, [])

    def test_fires_forced(self):
        self.t.schedule("reboot", 1)
        with self.t.lock:
            self.t.timer["at"] = time.time() - 1
        time.sleep(2.5)                    # o laço verifica a cada 2 s
        self.assertEqual(self.calls, [("reboot", True)])   # sempre forçado

    def test_validation(self):
        for a, m in (("halt", 10), ("poweroff", 0), ("poweroff", 99999), ("poweroff", "x")):
            with self.assertRaises(power.PowerError):
                self.t.schedule(a, m)


class ModeRevertTest(unittest.TestCase):
    def setUp(self):
        self.applied = []
        self._orig = (display._kscreen, display.outputs, display.REVERT_AFTER)
        display._kscreen = lambda *a, **k: self.applied.append(a[0])
        display.outputs = lambda: [{"name": "DP-1", "current": {"id": "1"},
                                    "modes": [{"id": "1", "w": 3440, "h": 1440, "hz": 160}, {"id": "4", "w": 3440, "h": 1440, "hz": 60}]}]
        display.REVERT_AFTER = 0.3
        self.m = display.ModeChanger()

    def tearDown(self):
        display._kscreen, display.outputs, display.REVERT_AFTER = self._orig

    def test_auto_revert(self):
        self.m.apply("DP-1", "4")
        time.sleep(0.6)
        self.assertEqual(self.applied, ["output.DP-1.mode.4", "output.DP-1.mode.1"])
        self.assertIsNone(self.m.status())

    def test_confirm_keeps(self):
        self.m.apply("DP-1", "4")
        self.m.confirm()
        time.sleep(0.6)
        self.assertEqual(self.applied, ["output.DP-1.mode.4"])

    def test_rejects_unknown_mode_and_injection(self):
        for out, mode in (("DP-1", "99"), ("DP-1;rm", "4"), ("DP-1", "4 --dpms")):
            with self.assertRaises(display.DisplayError):
                self.m.apply(out, mode)


class AlertsTest(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self._orig = (al.CONF_FILE, al.LOG_FILE)
        al.CONF_FILE, al.LOG_FILE = d / "a.json", d / "l.json"
        ctx = Ctx()
        ctx.data, ctx.proctrack, ctx.netmon, ctx.updates = {}, None, None, None
        self.ctx = ctx
        self.a = al.Alerts(ctx)
        self.a._deliver = lambda a, pc=True: None

    def tearDown(self):
        al.CONF_FILE, al.LOG_FILE = self._orig

    def test_disk_rule_and_cooldown(self):
        self.ctx.data = {"disks": {"partitions": [{"mount": "/", "used": 95, "total": 100}]}}
        self.a._check(heavy=False)
        self.a._check(heavy=False)
        self.assertEqual(self.a.unread(), 1)

    def test_disabled_rule(self):
        self.a.update_config({"rules": {"disk_full": {"enabled": False}}})
        self.ctx.data = {"disks": {"partitions": [{"mount": "/", "used": 95, "total": 100}]}}
        self.a._check(heavy=False)
        self.assertEqual(self.a.unread(), 0)

    def test_temperature_must_be_sustained(self):
        self.ctx.data = {"cpu": {"temp": 95}}
        self.a._check(heavy=False)
        self.assertEqual(self.a.unread(), 0)
        self.a._since["cpu_temp"] -= 31
        self.a._check(heavy=False)
        self.assertEqual(self.a.unread(), 1)

    def test_threshold_config(self):
        self.a.update_config({"rules": {"cpu_temp": {"value": 70}}})
        self.assertEqual(self.a._val("cpu_temp"), 70)
        with self.assertRaises(ValueError):
            self.a.update_config({"channels": {"ntfy": "ftp://x"}})


class GameCsvTest(unittest.TestCase):
    def test_parse_rows_by_header(self):
        lines = ["os,cpu,gpu", "Arch,AMD,AMD", "fps,frametime,gpu_load", "60.0,16.6,90", "30.5,32.8,99", "lixo"]
        header, rows = game.parse_rows(lines, None)
        self.assertEqual(header, ["fps", "frametime", "gpu_load"])
        self.assertEqual([r["fps"] for r in rows], [60.0, 30.5])

    def test_strip_block(self):
        text = "a=1\n\n" + "\n".join([game.BEGIN, "autostart_log=1", game.END]) + "\n"
        self.assertEqual(game.strip_block(text), "a=1\n")


class AudioTest(unittest.TestCase):
    def test_kind_and_volume(self):
        self.assertEqual(audio._kind({}, "alsa_output.pci.hdmi-stereo"), "hdmi")
        self.assertEqual(audio._kind({}, "HyperX Cloud Alpha"), "headset")
        self.assertEqual(audio._kind({"device.bus": "bluetooth"}, "bluez_output.x"), "bluetooth")
        self.assertEqual(audio._vol({"l": {"value_percent": "40%"}, "r": {"value_percent": "60%"}}), 50)

    def test_rejects_bad_input(self):
        for body in ({"op": "volume", "kind": "sink", "name": "x; rm -rf /", "volume": 10},
                     {"op": "volume", "kind": "sink", "name": "ok", "volume": 500},
                     {"op": "nope"}):
            with self.assertRaises(audio.AudioError):
                orig = audio._pactl
                audio._pactl = lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria chamar pactl"))
                try:
                    audio.action(body)
                finally:
                    audio._pactl = orig


class UpdatesTest(unittest.TestCase):
    def test_parse_and_sensitive(self):
        items = updates._arrow("linux-cachyos 7.3.1-1 -> 7.3.2-1\nkmod 34-1 -> 34-2\n", "pacman")
        self.assertEqual([i["name"] for i in items], ["linux-cachyos", "kmod"])
        self.assertEqual(updates.sensitive("linux-cachyos"), "kernel")
        self.assertIsNone(updates.sensitive("linux-cachyos-headers"))
        self.assertEqual(updates.sensitive("nct6687d-dkms-git"), "driver")
        self.assertIsNone(updates.sensitive("kmod"))


if __name__ == "__main__":
    unittest.main()
