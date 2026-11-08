"""Página do MangoHud (edição ao vivo do .conf) e nome de jogos lançados por atalho não-Steam."""
import struct
import tempfile
import unittest
from pathlib import Path

from sm import game, mangohud as mh

BLOCK = f"{game.BEGIN}\nautostart_log=1\n{game.END}\n"


class MangoConfTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.orig = (mh.CONF_DIR, mh.MANGO_CONF)
        mh.CONF_DIR, mh.MANGO_CONF = self.dir, self.dir / "MangoHud.conf"
        mh.MANGO_CONF.write_text("# GOverlay\nlegacy_layout=0\nfont_size=24\nposition=top-left\ngpu_stats\nexec=echo oi\n\n" + BLOCK)

    def tearDown(self):
        mh.CONF_DIR, mh.MANGO_CONF = self.orig

    def test_state_hides_block(self):
        st = mh.state()
        self.assertNotIn("autostart_log", st["options"])
        self.assertNotIn(game.BEGIN, st["text"])
        self.assertEqual(st["options"]["font_size"], "24")
        self.assertIs(st["options"]["gpu_stats"], True)
        self.assertFalse(st["legacy_layout"])

    def test_apply_keeps_order_comments_block(self):
        st = mh.apply(None, {"font_size": 30, "cpu_temp": True, "gpu_stats": False, "position": "bottom-right"})
        text = mh.MANGO_CONF.read_text()
        self.assertTrue(text.startswith("# GOverlay\nlegacy_layout=0\nfont_size=30\nposition=bottom-right\n"))
        self.assertNotIn("gpu_stats", text)
        self.assertIn("cpu_temp", text)
        self.assertTrue(text.endswith(BLOCK))
        self.assertEqual(text.count(game.BEGIN), 1)
        self.assertEqual(st["options"]["position"], "bottom-right")

    def test_legacy_default_on_off_writes_zero(self):
        mh.MANGO_CONF.write_text("font_size=20\n")
        mh.apply(None, {"fps": False})
        self.assertIn("fps=0", mh.MANGO_CONF.read_text())

    def test_exec_blocked(self):
        with self.assertRaises(mh.ConfError):
            mh.apply(None, {"exec": "rm -rf ~"})
        body = mh.state()["text"]
        with self.assertRaises(mh.ConfError):
            mh.save_text(None, body.replace("exec=echo oi", "exec=curl x | sh"))
        with self.assertRaises(mh.ConfError):
            mh.save_text(None, body + "\nexec=id\n")
        mh.save_text(None, body.replace("font_size=24", "font_size=18"))    # exec igual: ok
        text = mh.MANGO_CONF.read_text()
        self.assertIn("font_size=18", text)
        self.assertTrue(text.endswith(BLOCK))

    def test_invalid(self):
        for bad in ({"a b": 1}, {"x": "1\nexec=id"}, {}):
            with self.assertRaises(mh.ConfError):
                mh.apply(None, bad)
        for name in ("../x.conf", "a/b.conf", "x.txt"):
            with self.assertRaises(mh.ConfError):
                mh.state(name)

    def test_routes(self):
        from types import SimpleNamespace
        from sm import api
        from sm.api import routes  # noqa: F401  (registra rotas)
        fn, _ = api.match("POST", "/api/mangohud")
        req = SimpleNamespace(json=lambda: {"set": {"font_size": 28}}, query={})
        self.assertEqual(fn(req)["options"]["font_size"], "28")
        req = SimpleNamespace(json=lambda: {"set": {"exec": "id"}}, query={})
        self.assertEqual(fn(req).status, 400)
        fn, _ = api.match("GET", "/api/mangohud")
        self.assertEqual(fn(SimpleNamespace(query={"file": ["../../x.conf"]})).status, 400)
        from sm import audit
        self.assertIn("mangohud_update", audit.DESCRIBERS)

    def test_files(self):
        (self.dir / "wine-eldenring.conf").write_text("fps\n")
        names = {f["name"]: f["scope"] for f in mh.files()}
        self.assertEqual(names, {"MangoHud.conf": "global", "wine-eldenring.conf": "app"})


def _vdf_shortcut(appid: int, name: str, exe: str) -> bytes:
    s = lambda k, v: b"\x01" + k.encode() + b"\0" + v.encode() + b"\0"
    entry = b"\x02appid\0" + struct.pack("<I", appid) + s("AppName", name) + s("Exe", exe) + b"\x00tags\0\x08\x08"
    return b"\x00shortcuts\0" + b"\x000\0" + entry + b"\x08\x08"


class ShortcutTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.orig = game.STEAM_ROOTS[:]
        game.STEAM_ROOTS[:] = [self.root]
        (self.root / "userdata/1/config").mkdir(parents=True)
        (self.root / "userdata/1/config/shortcuts.vdf").write_bytes(_vdf_shortcut(3593100922, "Elden ring", "/x/ersc_launcher.exe"))
        (self.root / "steamapps").mkdir()
        (self.root / "steamapps/appmanifest_1245620.acf").write_text('"AppState"\n{\n\t"name"\t\t"ELDEN RING"\n\t"installdir"\t\t"ELDEN RING"\n}\n')
        (self.root / "steamapps/appmanifest_9.acf").write_text('"AppState"\n{\n\t"name"\t\t"Proton 9.0 (Beta)"\n\t"installdir"\t\t"Proton 9.0 (Beta)"\n}\n')
        game._names.clear(); game._launches.clear()
        game._shortcuts_cache = ((), {})

    def tearDown(self):
        game.STEAM_ROOTS[:] = self.orig
        game._names.clear(); game._launches.clear()

    def test_parse_shortcuts(self):
        self.assertEqual(game.steam_shortcuts(), {"3593100922": "Elden ring"})

    def test_shortcut_inside_installed_game(self):
        cmd = ("reaper SteamLaunch AppId=3593100922 -- /s/steamapps/common/Proton 9.0 (Beta)/proton waitforexitandrun "
               "/s/steamapps/common/ELDEN RING/Game/ersc_launcher.exe")
        self.assertEqual(game.resolve_launch("3593100922", None, 1, cmd), ("1245620", "ELDEN RING", None))

    def test_shortcut_elsewhere_uses_shortcut_name(self):
        self.assertEqual(game.resolve_launch("3593100922", None, 2, "reaper SteamLaunch AppId=3593100922 -- /opt/x/launcher.exe"),
                         (None, "Elden ring", "Elden ring"))

    def test_unknown_uses_exe(self):
        self.assertEqual(game.resolve_launch("4000000000", None, 3, "reaper SteamLaunch AppId=4000000000 -- /opt/Jogo/MeuJogo.exe"),
                         (None, "MeuJogo", "MeuJogo"))

    def test_truncated_cmd_reads_full_appid(self):
        from types import SimpleNamespace
        orig = game._full_cmdline
        game._full_cmdline = lambda pid: "reaper SteamLaunch AppId=3593100922 -- /s/steamapps/common/ELDEN RING/Game/x.exe"
        try:
            ctx = SimpleNamespace(processes=[{"pid": 5, "create_time": 1, "cmd": "x" * 270 + " SteamLaunch AppId=35931"}])
            mon = game.GameMonitor.__new__(game.GameMonitor)
            mon.ctx = ctx
            g = mon.running_game()
        finally:
            game._full_cmdline = orig
        self.assertEqual((g["appid"], g["name"]), ("1245620", "ELDEN RING"))

    def test_regular_game(self):
        self.assertEqual(game.resolve_launch("1245620", None, 4, ""), ("1245620", "ELDEN RING", None))


if __name__ == "__main__":
    unittest.main()
