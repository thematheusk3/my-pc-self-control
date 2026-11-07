import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests.helpers  # noqa: F401  (ajusta o sys.path)

from sm import auth, game, mural
from sm.game import game_key
from sm.mural import Mural, describe_weather, parse_weather, valid_key

RAW = {
    "current": {"temperature_2m": 22.1, "apparent_temperature": 23.6, "relative_humidity_2m": 70, "weather_code": 2,
                "is_day": 0, "wind_speed_10m": 9.4},
    "daily": {"time": ["2026-11-04", "2026-11-05"], "weather_code": [61, 0], "temperature_2m_max": [30.0, 31.5],
              "temperature_2m_min": [18.0, 19.1], "precipitation_probability_max": [60, 0],
              "sunrise": ["2026-11-04T05:27", "2026-11-05T05:26"], "sunset": ["2026-11-04T18:21", "2026-11-05T18:22"]},
}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        for mod, name, val in ((mural, "CONF_FILE", tmp / "mural.json"), (mural, "ART_DIR", tmp / "art"), (auth, "DATA", tmp),
                               (mural, "STEAM_ROOTS", [tmp / "steam"])):
            p = mock.patch.object(mod, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tmp


class WeatherTest(unittest.TestCase):
    def test_parse(self):
        w = parse_weather(RAW)
        self.assertEqual((w["temp"], w["label"], w["icon"]), (22.1, "Parcialmente nublado", "cloud-moon"))
        self.assertEqual(w["days"][0]["label"], "Chuva fraca")
        self.assertEqual(w["days"][1]["max"], 31.5)
        self.assertEqual(w["days"][0]["sunset"], "2026-11-04T18:21")

    def test_parse_without_sun_times(self):
        raw = {**RAW, "daily": {k: v for k, v in RAW["daily"].items() if k not in ("sunrise", "sunset")}}
        self.assertIsNone(parse_weather(raw)["days"][1]["sunrise"])

    def test_unknown_code_does_not_break(self):
        self.assertEqual(describe_weather(1234)[0], "Tempo indefinido")


class PreferencesTest(_Tmp):
    def test_starts_without_city_and_never_calls_the_network(self):
        m = Mural()
        with mock.patch.object(mural, "_fetch_json") as f:
            st = m.status()
        self.assertIsNone(st["location"])
        self.assertIsNone(st["weather"])
        self.assertEqual(st["mode"], "auto")
        f.assert_not_called()

    def test_choosing_city_fetches_weather_and_persists(self):
        m = Mural()
        with mock.patch.object(mural, "_fetch_json", return_value=RAW):
            st = m.update({"location": {"name": "Garça", "admin": "São Paulo", "country": "Brasil", "lat": -22.2, "lon": -49.6}})
        self.assertEqual(st["weather"]["temp"], 22.1)
        self.assertEqual(Mural().conf["location"]["name"], "Garça")

    def test_event_options_default_on_and_persist(self):
        m = Mural()
        self.assertTrue(m.status()["takeover"])
        m.update({"takeover": False, "context": False})
        self.assertFalse(Mural().status()["takeover"])
        self.assertFalse(Mural().status()["context"])

    def test_rejects_bad_values(self):
        m = Mural()
        for body in ({"mode": "x"}, {"location": {"lat": 200, "lon": 0}}, {"location": {"name": "x"}}):
            with self.assertRaises(ValueError):
                m.update(body)

    def test_game_options_merge_and_ignore_unknown(self):
        m = Mural()
        st = m.update({"game": {"ram": True, "fps": False, "invasor": True}})
        self.assertTrue(st["game"]["ram"])
        self.assertFalse(st["game"]["fps"])
        self.assertNotIn("invasor", st["game"])
        self.assertTrue(st["game"]["music"])

    def test_background_defaults_and_validates(self):
        m = Mural()
        self.assertEqual(m.status()["background"], "sky")
        m.update({"background": "board"})
        self.assertEqual(Mural().status()["background"], "board")
        with self.assertRaises(ValueError):
            m.update({"background": "invasor"})
        with self.assertRaises(ValueError):             # sem imagem enviada, não há o que mostrar
            m.update({"background": "photo"})

    def test_background_image_upload_and_removal(self):
        m = Mural()
        self.assertIsNone(m.background())
        with self.assertRaises(ValueError):
            m.set_background("data:image/png;base64," + base64.b64encode(b"nao-e-jpeg").decode())
        st = m.set_background("data:image/jpeg;base64," + base64.b64encode(JPEG).decode())
        self.assertEqual(st["background"], "photo")
        self.assertTrue(st["background_v"])
        self.assertEqual(Mural().background(), JPEG)
        st = m.reset_background()
        self.assertEqual((st["background"], st["background_v"]), ("sky", 0))
        self.assertIsNone(m.background())

    def test_weather_failure_keeps_last_reading(self):
        m = Mural()
        with mock.patch.object(mural, "_fetch_json", return_value=RAW):
            m.update({"location": {"lat": 1, "lon": 2}})
        with mock.patch.object(mural, "_fetch_json", side_effect=OSError("sem rede")):
            m.refresh_weather()
        self.assertEqual(m.weather_data["temp"], 22.1)
        self.assertIn("clima", m.weather_error)


class ArtTest(_Tmp):
    def steam_file(self, appid, name, sub=""):
        d = self.tmp / "steam" / "appcache" / "librarycache" / appid / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"local")

    def test_keys(self):
        self.assertTrue(valid_key("steam:1245620"))
        self.assertTrue(valid_key("exe:meu-jogo.exe"))
        for k in ("", "steam:abc", "exe:../../x", "exe:A B", "file:x"):
            self.assertFalse(valid_key(k), k)
        self.assertEqual(game_key({"appid": "10"}), "steam:10")
        self.assertEqual(game_key({"appid": None, "exe": "Meu Jogo.exe"}), "exe:meu-jogo.exe")
        self.assertTrue(valid_key(game_key({"appid": None, "name": "Ação & Aventura!"})))

    def test_steam_local_cache_first_including_subfolder(self):
        self.steam_file("10", "library_hero.jpg")
        self.steam_file("20", "library_600x900.jpg", "abc123")
        m = Mural()
        with mock.patch.object(mural.urllib.request, "urlopen") as net:
            self.assertEqual(m.art("steam:10"), (b"local", "image/jpeg"))
            self.assertEqual(m.art("steam:20", "cover")[0], b"local")
        net.assert_not_called()

    def test_new_games_use_other_names_in_hashed_subfolders(self):
        self.steam_file("50", "library_header.jpg", "abc")
        self.steam_file("50", "library_capsule.jpg", "def")
        m = Mural()
        with mock.patch.object(mural.urllib.request, "urlopen") as net:
            self.assertEqual(m.art("steam:50", "header")[0], b"local")
            self.assertEqual(m.art("steam:50", "cover")[0], b"local")
        net.assert_not_called()

    def test_cdn_fallback_is_cached_and_misses_are_remembered(self):
        m = Mural()
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b"cdn"
        with mock.patch.object(mural.urllib.request, "urlopen", return_value=resp) as net:
            self.assertEqual(m.art("steam:30")[0], b"cdn")
            self.assertIn("/steam/apps/30/library_hero.jpg", net.call_args[0][0].full_url)
            self.assertEqual(m.art("steam:30")[0], b"cdn")
        self.assertEqual(net.call_count, 1)
        with mock.patch.object(mural.urllib.request, "urlopen", side_effect=OSError) as net:
            self.assertIsNone(m.art("steam:40"))
            self.assertIsNone(m.art("steam:40"))
        self.assertEqual(net.call_count, 1)

    def test_unknown_kind_and_bad_key_never_reach_the_network(self):
        m = Mural()
        with mock.patch.object(mural.urllib.request, "urlopen") as net:
            self.assertIsNone(m.art("steam:10", "../../etc"))
            self.assertIsNone(m.art("exe:../../x"))
            self.assertIsNone(m.art("exe:jogo"))
        net.assert_not_called()

    def test_upload_choice_and_reset(self):
        m = Mural()
        m.set_art("exe:jogo.exe", "Jogo", image="data:image/jpeg;base64," + base64.b64encode(JPEG).decode())
        self.assertEqual(m.art("exe:jogo.exe"), (JPEG, "image/jpeg"))
        g = next(x for x in Mural().games() if x["key"] == "exe:jogo.exe")
        self.assertEqual((g["name"], g["kind"]), ("Jogo", "custom"))
        m.reset_art("exe:jogo.exe")
        self.assertIsNone(m.art("exe:jogo.exe"))

    def test_upload_rejects_what_is_not_a_small_jpeg(self):
        m = Mural()
        b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
        for image in ("%%%", b64(b"<svg onload=x>"), b64(b"\xff\xd8\xff" + b"0" * mural.MAX_UPLOAD)):
            with self.assertRaises(ValueError):
                m.set_art("steam:10", "x", image=image)
        with self.assertRaises(ValueError):
            m.set_art("exe:jogo", "x", kind="hero")     # fora da Steam só com imagem
        with self.assertRaises(ValueError):
            m.set_art("steam:10", "x", kind="logo")

    def test_games_merge_installed_custom_and_current(self):
        m = Mural()
        with mock.patch.object(mural, "steam_games", return_value=[("10", "Zeta"), ("20", "alfa")]):
            m.set_art("steam:20", "alfa", kind="cover")
            items = m.games({"key": "exe:novo", "name": "Novo"})
        self.assertEqual([g["name"] for g in items], ["alfa", "Novo", "Zeta"])
        self.assertEqual(items[0]["kind"], "cover")
        self.assertEqual(items[2]["kind"], "hero")


class SteamGamesTest(unittest.TestCase):
    def test_lists_games_without_redistributables(self):
        root = Path(tempfile.mkdtemp())
        apps = root / "steamapps"
        apps.mkdir()
        (apps / "appmanifest_10.acf").write_text('"AppState"\n{\n"name"\t\t"Elden Ring"\n}\n', encoding="utf-8")
        (apps / "appmanifest_228980.acf").write_text('"name" "Steamworks Common Redistributables"', encoding="utf-8")
        with mock.patch.object(game, "STEAM_ROOTS", [root]):
            self.assertEqual(game.steam_games(), [("10", "Elden Ring")])


class ConfFileTest(_Tmp):
    def test_corrupt_file_falls_back_to_defaults(self):
        mural.CONF_FILE.write_text("{quebrado")
        self.assertEqual(Mural().conf["mode"], "auto")

    def test_invalid_mode_on_disk(self):
        mural.CONF_FILE.write_text(json.dumps({"mode": "x"}))
        self.assertEqual(Mural().conf["mode"], "auto")


if __name__ == "__main__":
    unittest.main()
