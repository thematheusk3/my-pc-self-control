import unittest
from unittest import mock

import tests.helpers  # noqa: F401  (ajusta o sys.path)

from sm import selfupdate
from sm.selfupdate import SelfUpdate, compare, parse_changelog, parse_version, remote_version

LOG = """# Changelog

## 0.7.0-win
- Primeiro tópico,
  que continua aqui.
- Segundo com **negrito**.

## 0.5.0-win
Parágrafo sem tópico.
- Tópico.
"""


class VersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_version("0.6.0-win"), (0, 6, 0))
        self.assertEqual(parse_version("v1.2"), (1, 2))
        self.assertIsNone(parse_version("beta"))
        self.assertIsNone(parse_version(None))

    def test_compare_is_numeric_not_text(self):
        self.assertEqual(compare("0.9.0-win", "0.10.0-win"), "update")
        self.assertEqual(compare("0.10.0-win", "0.9.0-win"), "ahead")
        self.assertEqual(compare("0.6.0-win", "0.6.0-win"), "current")
        self.assertEqual(compare("0.6-win", "0.6.0-win"), "current")
        self.assertEqual(compare("0.6.0-win", None), "unknown")

    def test_remote_version(self):
        self.assertEqual(remote_version('"""doc"""\n__version__ = "0.7.0-win"\n'), "0.7.0-win")
        self.assertIsNone(remote_version("<html>página de erro</html>"))


class ChangelogTest(unittest.TestCase):
    def test_parse(self):
        e = parse_changelog(LOG)
        self.assertEqual([x["version"] for x in e], ["0.7.0-win", "0.5.0-win"])
        self.assertEqual(e[0]["items"], ["Primeiro tópico, que continua aqui.", "Segundo com **negrito**."])
        self.assertEqual(e[1]["items"], ["Parágrafo sem tópico.", "Tópico."])

    def test_changelog_date(self):
        e = parse_changelog("# Changelog\n\n## 1.2.0 — 2026-11-08\n- a\n\n## 1.1.0\n- b\n")
        self.assertEqual([(x["version"], x["date"]) for x in e], [("1.2.0", "2026-11-08"), ("1.1.0", None)])

    def test_real_changelog_has_installed_version(self):
        e = parse_changelog(selfupdate.CHANGELOG.read_text(encoding="utf-8"))
        self.assertEqual(e[0]["version"], selfupdate.__version__, "CHANGELOG.md sem a versão atual")
        self.assertTrue(all(x["items"] for x in e))


class CheckTest(unittest.TestCase):
    def make(self, responses, t=1000.0, enabled=True):
        u = SelfUpdate("dono/repo", enabled=enabled, now=lambda: t)
        u._get = mock.Mock(side_effect=responses)
        return u

    def test_new_version_is_flagged_with_remote_changelog(self):
        u = self.make(['__version__ = "99.0.0-win"\n', "## 99.0.0-win\n- Novidade.\n"])
        u.check()
        st = u.status()
        self.assertEqual((st["state"], st["latest"], st["error"]), ("update", "99.0.0-win", None))
        self.assertTrue(st["changelog"][0]["new"])
        self.assertFalse(st["changelog"][0]["installed"])

    def test_failure_keeps_last_result_and_explains(self):
        u = self.make(['__version__ = "99.0.0-win"\n', "## 99.0.0-win\n- Novidade.\n", OSError("sem rede")])
        u.check()
        u.check()
        st = u.status()
        self.assertEqual((st["state"], st["latest"]), ("update", "99.0.0-win"))
        self.assertIn("GitHub", st["error"])

    def test_error_page_is_not_a_version(self):
        u = self.make(["<html>erro</html>"])
        u.check()
        self.assertEqual(u.status()["state"], "unknown")
        self.assertTrue(u.status()["error"])

    def test_changelog_failure_does_not_hide_version(self):
        u = self.make(['__version__ = "99.0.0-win"\n', OSError("sem rede")])
        u.check()
        st = u.status()
        self.assertEqual((st["state"], st["error"]), ("update", None))
        self.assertTrue(st["changelog"], "usa o changelog local")

    def test_disabled_never_touches_network(self):
        u = self.make([], enabled=False)
        u.status()
        u.check_now()
        self.assertEqual(u.status()["state"], "off")
        u._get.assert_not_called()

    def test_manual_check_is_rate_limited(self):
        u = self.make(['__version__ = "1.0"\n', "## 1.0\n- x\n"])
        u.check_now()
        u.check_now()                     # mesmo instante: não consulta de novo
        self.assertEqual(u._get.call_count, 2)

    def test_status_starts_one_background_check_when_stale(self):
        u = self.make([], t=1e6)
        with mock.patch.object(selfupdate.threading, "Thread") as t:
            self.assertTrue(u.status()["checking"], "a página precisa saber que a resposta ainda vem")
            t.assert_called_once()
            u.checked = 1e6               # recente: não consulta
            t.reset_mock()
            u.status()
            t.assert_not_called()


if __name__ == "__main__":
    unittest.main()
