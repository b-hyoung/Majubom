"""리더 시작 절차 시험: python -m unittest mmWave/tests/test_reader_startup.py"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import send_mmw  # noqa: E402


class FakePort:
    opened = []
    chunks = []
    error = None

    def __init__(self, port, baud, timeout=None, exclusive=None):
        if FakePort.error:
            raise FakePort.error
        FakePort.opened.append((port, exclusive))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n):
        return FakePort.chunks.pop(0) if FakePort.chunks else b""


class ConfigTests(unittest.TestCase):
    def setUp(self):
        FakePort.opened, FakePort.chunks, FakePort.error = [], [], None
        patches = [mock.patch.object(send_mmw, "serial", types.SimpleNamespace(Serial=FakePort)),
                   mock.patch.object(send_mmw, "send_config"),
                   mock.patch.object(send_mmw, "STREAM_CHECK_SEC", 0.05)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.sent = send_mmw.send_config

    def apply(self):
        with mock.patch.object(send_mmw.stream_active, "__defaults__", (0.05, 921600)):
            return send_mmw.apply_config("cli", "data", "x.cfg")

    def test_config_is_skipped_while_frames_are_flowing(self):
        FakePort.chunks = [b"\0\0" + send_mmw.MAGIC]
        self.assertFalse(self.apply())
        self.sent.assert_not_called()

    def test_config_is_sent_when_the_radar_is_silent(self):
        self.assertTrue(self.apply())
        self.sent.assert_called_once_with("cli", "x.cfg")

    def test_data_port_is_opened_exclusively(self):
        self.apply()
        self.assertEqual(FakePort.opened, [("data", True)])

    def test_config_is_not_sent_when_another_process_holds_the_port(self):
        FakePort.error = OSError("port busy")
        with self.assertRaises(OSError):
            self.apply()
        self.sent.assert_not_called()


class QualityTests(unittest.TestCase):
    def test_quality_uses_the_field_name_the_server_reads(self):
        samples = [(i * 0.05, i * 0.05, 0.0, 1.0, 1.0, 0.0) for i in range(30)]
        _, quality, _ = send_mmw.select_patient({1: samples}, 1.5)
        self.assertEqual(quality["samples_count"], 30)
        self.assertNotIn("n_samples", quality)
        _, quality, _ = send_mmw.select_patient({1: samples[-3:]}, 1.5)
        self.assertEqual(quality["samples_count"], 3)


if __name__ == "__main__":
    unittest.main()
