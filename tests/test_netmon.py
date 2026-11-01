import unittest

import tests.helpers  # noqa: F401
from sm import netmon


class ParseTest(unittest.TestCase):
    def test_ipv4_with_bytes_and_process(self):
        line = ('ESTAB 0 0 192.168.1.50:53566 34.158.255.62:4070 users:(("spotify",pid=4209,fd=117)) '
                'cubic bytes_sent:1448 bytes_acked:1449 bytes_received:16846 segs_out:96')
        c = netmon.parse(line, "tcp")
        self.assertEqual((c["lip"], c["lport"], c["rip"], c["rport"]), ("192.168.1.50", 53566, "34.158.255.62", 4070))
        self.assertEqual(c["procs"], [{"name": "spotify", "pid": 4209}])
        self.assertEqual((c["sent"], c["recv"]), (1449, 16846))

    def test_ipv6_interface_and_listen(self):
        c = netmon.parse("LISTEN 0 128 [::]:22 [::]:* cubic", "tcp")
        self.assertEqual((c["lip"], c["lport"], c["rport"]), ("::", 22, None))
        c = netmon.parse("ESTAB 0 0 192.168.1.50%wlan0:68 192.168.1.1:67", "udp")
        self.assertEqual(c["lip"], "192.168.1.50")
        self.assertIsNone(c["sent"])

    def test_shared_socket_unique_pids(self):
        c = netmon.parse('ESTAB 0 0 1.1.1.1:1 2.2.2.2:2 users:(("a",pid=5,fd=1),("a",pid=5,fd=2),("b",pid=6,fd=3))', "tcp")
        self.assertEqual([p["pid"] for p in c["procs"]], [5, 6])

    def test_classify_and_service(self):
        self.assertEqual(netmon.classify("127.0.0.1"), "local")
        self.assertEqual(netmon.classify("192.168.0.10"), "lan")
        self.assertEqual(netmon.classify("8.8.8.8"), "internet")
        self.assertEqual(netmon.classify("::ffff:192.168.0.1"), "lan")
        self.assertEqual(netmon.service(443, "tcp"), "HTTPS")
        self.assertIsNone(netmon.service(8765, "tcp"))   # porta alta sem nome conhecido


if __name__ == "__main__":
    unittest.main()
