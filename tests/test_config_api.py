import tomllib
import unittest

from tests.helpers import ROOT, Ctx

from sm import api, config
from sm.api import routes  # noqa: F401  (registra rotas)


class ConfigTest(unittest.TestCase):
    def test_example_config_loads(self):
        cfg = config.load(ROOT / "config.example.toml")
        self.assertEqual(cfg.port, 8765)
        self.assertEqual(cfg.fan_chip, "auto")
        with open(ROOT / "config.example.toml", "rb") as f:
            self.assertIn("security", tomllib.load(f))

    def test_defaults_without_file(self):
        self.assertTrue(Ctx().config.auth_enabled)


class RouterTest(unittest.TestCase):
    def test_route_params_are_unquoted(self):
        fn, params = api.match("POST", "/api/apps/ELDEN%20RING.desktop/launch")
        self.assertIsNotNone(fn)
        self.assertEqual(params, {"aid": "ELDEN RING.desktop"})

    def test_method_not_allowed(self):
        self.assertEqual(api.match("DELETE", "/api/info"), (None, "method"))


if __name__ == "__main__":
    unittest.main()
