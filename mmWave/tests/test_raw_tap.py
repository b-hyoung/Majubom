"""원본 바이트 복사(RawTap) 시험: python -m unittest mmWave/tests/test_raw_tap.py

DATA 포트는 한 프로세스만 열 수 있다. 리더가 읽은 바이트를 127.0.0.1로 복사해 주면
테스트 뷰어가 리더와 함께 돌 수 있다. 복사에 실패해도 리더는 멈추면 안 된다.
"""
import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from send_mmw import RawTap  # noqa: E402


def wait_clients(tap, n, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        with tap.lock:
            if len(tap.clients) == n:
                return True
        time.sleep(0.01)
    return False


def recv_exact(sock, n, timeout=2.0):
    sock.settimeout(timeout)
    out = b""
    while len(out) < n:
        b = sock.recv(n - len(out))
        if not b:
            break
        out += b
    return out


class RawTapTests(unittest.TestCase):
    def setUp(self):
        self.tap = RawTap(0)   # 0: 빈 포트를 아무거나 받음

    def tearDown(self):
        self.tap.close()

    def test_client_receives_same_bytes(self):
        c = socket.create_connection(("127.0.0.1", self.tap.port))
        self.assertTrue(wait_clients(self.tap, 1))
        data = bytes(range(256)) * 20
        self.tap.send(data[:1000])
        self.tap.send(data[1000:])
        self.assertEqual(recv_exact(c, len(data)), data)
        c.close()

    def test_send_without_clients_is_noop(self):
        self.tap.send(b"\x02\x01\x04\x03" * 100)   # 오류 없이 넘어가야 한다

    def test_closed_client_is_dropped_others_keep_receiving(self):
        a = socket.create_connection(("127.0.0.1", self.tap.port))
        b = socket.create_connection(("127.0.0.1", self.tap.port))
        self.assertTrue(wait_clients(self.tap, 2))
        a.close()
        end = time.time() + 3
        while time.time() < end:   # 끊긴 쪽은 몇 번 보내야 오류가 난다
            self.tap.send(b"x" * 1024)
            with self.tap.lock:
                if len(self.tap.clients) == 1:
                    break
            time.sleep(0.02)
        with self.tap.lock:
            self.assertEqual(len(self.tap.clients), 1)
        b.settimeout(2)
        self.assertTrue(b.recv(10))   # 남은 쪽은 계속 받는다
        self.tap.send(b"END!")
        got = b""
        b.settimeout(2)
        while not got.endswith(b"END!"):
            got += b.recv(65536)
        b.close()

    def test_slow_client_does_not_block_reader(self):
        slow = socket.create_connection(("127.0.0.1", self.tap.port))   # 읽지 않는 뷰어
        slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        self.assertTrue(wait_clients(self.tap, 1))
        t0 = time.time()
        for _ in range(2000):   # 8MB: 소켓 버퍼를 넘긴다
            self.tap.send(b"y" * 4096)
        self.assertLess(time.time() - t0, 2.0)   # 리더는 기다리지 않는다
        with self.tap.lock:
            self.assertEqual(len(self.tap.clients), 0)   # 버퍼가 찬 뷰어는 내보냈다
        slow.close()


if __name__ == "__main__":
    unittest.main()
