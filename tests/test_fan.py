import tempfile
import unittest
from pathlib import Path

from tests.helpers import Ctx, fake_hwmon

from sm import fan as fanmod
from sm.hw import hwmon


class FanTest(unittest.TestCase):
    def setUp(self):
        self.hw = fake_hwmon({
            "hwmon2": {"name": "k10temp", "temp1_input": "60000", "temp1_label": "Tctl"},
            "hwmon4": {"name": "nct6686", "pwm1": "0", "pwm1_enable": "2", "fan1_input": "0",
                       "pwm2": "130", "pwm2_enable": "1", "fan2_input": "1400", "fan2_label": "Pump Fan"},
        })
        self._orig = (hwmon.HWMON, fanmod.STATE_FILE, fanmod.ACTIVE_MARKER)
        tmp = Path(tempfile.mkdtemp())
        hwmon.HWMON, fanmod.STATE_FILE, fanmod.ACTIVE_MARKER = self.hw, tmp / "fan.json", tmp / "fan.active"
        self.fc = fanmod.FanController(Ctx())
        self.chip = self.hw / "hwmon4"

    def tearDown(self):
        hwmon.HWMON, fanmod.STATE_FILE, fanmod.ACTIVE_MARKER = self._orig

    def tick(self, temp=None, n=1, dt=1.0):
        for _ in range(n):
            if temp is not None:
                (self.hw / "hwmon2" / "temp1_input").write_text(f"{int(temp * 1000)}\n")
            with self.fc._lock:
                self.fc._tick(dt)
        return int((self.chip / "pwm2").read_text()), int((self.chip / "pwm2_enable").read_text())

    def test_autodetect_spinning_channel(self):
        self.assertEqual((self.fc.dir, self.fc.n), (self.chip, 2))
        self.assertEqual(self.fc.label, "Pump Fan")

    def test_unmanaged_never_writes(self):
        self.assertEqual(self.tick(70, n=3), (130, 1))

    def test_auto_curve_and_slow_ramp_down(self):
        self.fc.update({"mode": "auto", "preset": "equilibrado"})
        self.tick(80, n=5)
        self.assertAlmostEqual(self.fc.target_pct, 75.7, places=1)
        self.tick(78, n=5)       # dentro da histerese: mantém
        self.assertAlmostEqual(self.fc.target_pct, 75.7, places=1)
        self.tick(55, n=3)       # desce no máximo step_down %/s
        self.assertGreater(self.fc.target_pct, 60)
        self.tick(55, n=30)
        self.assertLess(self.fc.target_pct, 35)

    def test_emergency_and_lost_temperature(self):
        self.fc.update({"mode": "manual", "manual_pct": 30})
        self.assertEqual(self.tick(95, n=2)[0], 255)
        self.assertIn("Emergência", self.fc.reason)
        self.fc._temps.clear()
        self.fc._last_temp_ok -= 60
        (self.hw / "hwmon2" / "temp1_input").write_text("0\n")
        from sm.collectors import cpu
        orig, cpu.cpu_temp = cpu.cpu_temp, lambda: (None, None)
        try:
            self.assertEqual(self.tick()[0], 255)
        finally:
            cpu.cpu_temp = orig

    def test_manual_respects_min(self):
        self.fc.update({"mode": "manual", "manual_pct": 5})
        self.assertEqual(self.tick(50)[0], fanmod.pct_to_pwm(self.fc.cfg.fan_min_pct))

    def test_firmware_and_release(self):
        self.fc.update({"mode": "manual", "manual_pct": 50})
        self.tick(50)
        self.assertTrue(fanmod.ACTIVE_MARKER.exists())
        self.fc.release()
        self.assertEqual(int((self.chip / "pwm2_enable").read_text()), 2)
        self.assertFalse(fanmod.ACTIVE_MARKER.exists())

    def test_validation(self):
        for bad in ({"mode": "x"}, {"curve": [[50, 50], [60, 40]]}, {"curve": [[50, 50]]},
                    {"manual_pct": 150}, {"preset": "nope"}):
            with self.assertRaises(ValueError, msg=bad):
                self.fc.update(bad)

    def test_interpolate(self):
        c = [[50, 20], [70, 60]]
        self.assertEqual(fanmod.interpolate(c, 40), 20)
        self.assertEqual(fanmod.interpolate(c, 60), 40)
        self.assertEqual(fanmod.interpolate(c, 90), 60)


if __name__ == "__main__":
    unittest.main()
