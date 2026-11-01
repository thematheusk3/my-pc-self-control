import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401
from sm import desktop


def write(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text("[Desktop Entry]\n" + body)
    return p


class DesktopTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_parse_localized_and_category(self):
        p = write(self.d, "a.desktop", "Type=Application\nName=Calc\nName[pt_BR]=Calculadora\nIcon=kcalc\n"
                                       "Categories=Utility;Calculator;\nExec=kcalc\n")
        a = desktop.Apps._parse(p, "a.desktop")
        self.assertTrue(a["show"])
        self.assertEqual(a["name"], "Calculadora")
        self.assertEqual(a["category"], "Utilitários")

    def test_hidden_entries(self):
        for body in ("Type=Application\nName=X\nNoDisplay=true\n", "Type=Application\nName=X\nOnlyShowIn=GNOME;\n",
                     "Type=Application\nName=X\nTryExec=/nao/existe\n"):
            self.assertFalse(desktop.Apps._parse(write(self.d, "h.desktop", body), "h.desktop")["show"], body)
        self.assertFalse(desktop.Apps._parse(write(self.d, "l.desktop", "Type=Link\nName=L\n"), "l.desktop")["show"])

    def test_steam_cache_icon(self):
        root = self.d / "Steam"
        cache = root / "appcache/librarycache/123"
        cache.mkdir(parents=True)
        (cache / "library_600x900.jpg").write_bytes(b"x")
        (cache / ("a" * 40 + ".jpg")).write_bytes(b"x")
        orig, desktop.STEAM_ROOTS = desktop.STEAM_ROOTS, [root]
        try:
            self.assertEqual(desktop.steam_cache_icon("123").name, "a" * 40 + ".jpg")
            self.assertIsNone(desktop.steam_cache_icon("999"))
        finally:
            desktop.STEAM_ROOTS = orig


if __name__ == "__main__":
    unittest.main()
