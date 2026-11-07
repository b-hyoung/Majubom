import json
import tempfile
import unittest
from pathlib import Path
from bed_pose.contracts import FrameKey
from bed_pose.recording import FrameLedger
from bed_pose.config import load_json

class FoundationTests(unittest.TestCase):
    def test_invalid_frame(self):
        with self.assertRaises(ValueError):
            FrameKey('run', 'video', True)

    def test_duplicate_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{"x":1,"x":2}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_json(path)

    def test_failure_can_have_complete_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = FrameLedger(Path(directory) / 'run', 3)
            ledger.append(0, 'ok')
            ledger.append(1, 'failed')
            ledger.append(2, 'not_processed')
            with self.assertRaises(ValueError):
                ledger.finalize('succeeded')
            ledger.finalize('failed')
            state = json.loads((ledger.directory / 'manifest.json').read_text(encoding='utf-8'))
            self.assertTrue(state['ledger_complete'])
            self.assertEqual(state['run_status'], 'failed')

    def test_incomplete_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = FrameLedger(Path(directory) / 'run', 2)
            ledger.append(0, 'ok')
            with self.assertRaises(ValueError):
                ledger.append(0, 'ok')
            ledger.close()
            state = json.loads((ledger.directory / 'manifest.json').read_text(encoding='utf-8'))
            self.assertFalse(state['ledger_complete'])
