import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bed_pose.contracts import SourceError
from bed_pose.live_sources import PiCameraSource


class PiCameraTests(unittest.TestCase):
    def test_stream_url_is_used_but_not_recorded_in_profile(self):
        class Capture:
            def isOpened(self): return True
            def release(self): pass
        with tempfile.TemporaryDirectory() as root, patch.object(Path, 'home', return_value=Path(root)):
            config = Path(root)/'.config'/'bed-pose'/'pi-camera-url.txt'
            config.parent.mkdir(parents=True)
            secret_url = 'http://192.168.0.4:8091/private-token'
            config.write_text(secret_url, encoding='ascii')
            with patch('cv2.VideoCapture', return_value=Capture()) as capture:
                source = PiCameraSource()
                profile = source.open()
                source.close()
            self.assertEqual(capture.call_args.args[0], secret_url)
            self.assertEqual(profile['kind'], 'camera')
            self.assertEqual(profile['camera_index'], 'raspberry_pi')
            self.assertNotIn('private-token', repr(profile))

    def test_invalid_stream_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as root, patch.object(Path, 'home', return_value=Path(root)):
            config = Path(root)/'.config'/'bed-pose'/'pi-camera-url.txt'
            config.parent.mkdir(parents=True)
            config.write_text('file:///unexpected', encoding='ascii')
            with self.assertRaises(SourceError): PiCameraSource().open()


if __name__ == '__main__': unittest.main()
