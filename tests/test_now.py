"""Downloads da Steam, mídia (MPRIS), janela em foco e captura de tela: partes sem dependência do sistema."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import helpers  # noqa: F401  (ajusta sys.path)
from sm import focus, media, screenshot, steamdl


class SteamLogTest(unittest.TestCase):
    def test_states(self):
        apps = {}
        for line in [
            "[x] AppID 5 state changed : Update Required,Fully Installed,Update Queued,Update Running,Update Started,",
            "[x] AppID 5 update started : download 100/2000, store 0/0, reuse 0/1, delta 0/0, stage 0/5000 ",
            "[x] AppID 5 update changed : Running Update,Downloading,Staging,",
            "[x] AppID 6 state changed : Update Required,Fully Installed,Update Queued,Update Started, (Suspended)",
            "[x] AppID 7 state changed : Update Required,Update Queued, (Update delayed for 30 secs)",
            "[x] AppID 8 state changed : Update Required,Update Queued,",
            "[x] AppID 9 state changed : Fully Installed,",
        ]:
            steamdl.parse_log_line(line, apps)
        st = {k: steamdl.status_of(v["flags"]) for k, v in apps.items()}
        self.assertEqual(st, {"5": "running", "6": "paused", "7": None, "8": "queued", "9": None})
        self.assertEqual((apps["5"]["dl_total"], apps["5"]["stage_total"], apps["5"]["phase"]), (2000, 5000, "baixando"))

    def test_acf(self):
        acf = '"AppState"\n{\n\t"appid"\t\t"5"\n\t"name"\t\t"Jogo X"\n\t"StateFlags"\t\t"1026"\n' \
              '\t"BytesToDownload"\t\t"1000"\n\t"BytesDownloaded"\t\t"250"\n}'
        d = steamdl.parse_acf(acf)
        self.assertEqual((d["name"], d["StateFlags"], d["BytesToDownload"], d["BytesDownloaded"]), ("Jogo X", 1026, 1000, 250))


class MediaTest(unittest.TestCase):
    def test_unwrap_and_parse(self):
        md = {"type": "a{sv}", "data": {"xesam:title": {"type": "s", "data": "Crawling"},
                                         "xesam:artist": {"type": "as", "data": ["Linkin Park"]},
                                         "mpris:length": {"type": "t", "data": 208960000}}}
        player = media.unwrap({"PlaybackStatus": {"type": "s", "data": "Playing"}, "Metadata": md,
                               "Position": {"type": "x", "data": 130073000}, "CanPause": {"type": "b", "data": True}})
        p = media.parse_player("org.mpris.MediaPlayer2.spotify", {"Identity": "Spotify"}, player)
        self.assertEqual((p["id"], p["app"], p["status"], p["title"], p["artist"]), ("spotify", "Spotify", "playing", "Crawling", "Linkin Park"))
        self.assertEqual((p["length"], p["position"], p["can"]["play_pause"], p["can"]["next"]), (209.0, 130.1, True, False))

    def test_control_validates(self):
        m = media.Media()
        with self.assertRaises(media.MediaError):
            m.control("spotify", "rm -rf")
        with self.assertRaises(media.MediaError):      # player que não está na lista
            m.control("../../etc", "next")


class FocusTest(unittest.TestCase):
    def test_parse_line(self):
        self.assertEqual(focus.parse_line('SMFOCUS {"caption":"a","pid":3}\n'), (True, {"caption": "a", "pid": 3}))
        self.assertEqual(focus.parse_line("SMFOCUS null"), (True, None))
        self.assertEqual(focus.parse_line("kwin: outra coisa"), (False, None))
        self.assertEqual(focus.parse_line("SMFOCUS [1,2]"), (False, None))


class ScreenshotTest(unittest.TestCase):
    def test_png_complete(self):
        d = Path(tempfile.mkdtemp())
        full = b"\x89PNG\r\n\x1a\n" + b"x" * 100 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
        (d / "a.png").write_bytes(full)
        (d / "b.png").write_bytes(full[:60])
        self.assertTrue(screenshot.png_complete(d / "a.png"))
        self.assertFalse(screenshot.png_complete(d / "b.png"))
        self.assertFalse(screenshot.png_complete(d / "nao-existe.png"))

    def test_names(self):
        self.assertIsNotNone(screenshot._NAME.match("shot-20261101-232608.png"))
        self.assertIsNotNone(screenshot._NAME.match("shot-20261101-232608-2.png"))
        self.assertIsNone(screenshot._NAME.match("../shot-20261101-232608.png"))


if __name__ == "__main__":
    unittest.main()
