"""Protect against selecting the built-in camera when an external camera is absent."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'mac_launcher', Path(__file__).resolve().parents[1]/'tools/start_mac_monitor.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class MacLauncherTests(unittest.TestCase):
    def test_missing_external_camera_does_not_fall_back_to_builtin(self):
        with self.assertRaises(ValueError):
            launcher.select_camera([dict(index=1200, name='MacBook Pro Camera')],
                                   'USB2.0 PC CAMERA')

    def test_duplicate_name_requires_user_selection(self):
        with self.assertRaises(ValueError):
            launcher.select_camera([dict(index=1200, name='USB2.0 PC CAMERA'),
                                    dict(index=1202, name='USB2.0 PC CAMERA')],
                                   'USB2.0 PC CAMERA')

    def test_external_device_uses_enumerator_backend_index(self):
        result = launcher.select_camera([dict(index=1201, name='MacBook Pro Camera'),
                                         dict(index=1200, name='USB2.0 PC CAMERA')],
                                        'USB2.0 PC CAMERA')
        self.assertEqual(result['index'], 1200)


if __name__ == '__main__':
    unittest.main()
