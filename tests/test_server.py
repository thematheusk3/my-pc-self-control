"""Servidor HTTP: keep-alive com corpo não lido pela rota."""
from __future__ import annotations

import socket
import threading
import unittest

from tests.helpers import Ctx

from sm.sampler import Context
from sm.server import make_server


class KeepAliveTest(unittest.TestCase):
    def setUp(self):
        cfg = Ctx(auth_enabled=False, host="127.0.0.1", port=0).config
        self.srv = make_server(Context(cfg))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _response(self, f) -> tuple[str, bytes]:
        status = f.readline().decode()
        n = 0
        while (line := f.readline()) not in (b"\r\n", b""):
            k, _, v = line.decode().partition(":")
            if k.lower() == "content-length":
                n = int(v)
        return status, f.read(n)

    def test_unread_body_does_not_leak_into_next_request(self):
        # rota POST que não lê o corpo (como /api/screenshot); depois um GET na MESMA conexão.
        # Antes: o "{}" ficava no socket e o GET chegava como "{}GET ..." -> 501.
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            f = s.makefile("rb")
            s.sendall(b"POST /api/screenshots/nao-existe.png/delete HTTP/1.1\r\nHost: x\r\n"
                      b"Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{}")
            status, _ = self._response(f)
            self.assertIn(" 200 ", status)
            s.sendall(b"GET /api/info HTTP/1.1\r\nHost: x\r\n\r\n")
            status, body = self._response(f)
            self.assertIn(" 200 ", status, body[:200])
            self.assertIn(b'"version"', body)


if __name__ == "__main__":
    unittest.main()
