"""The extra ToF snapshot branch must retain the original DB call.

2026-10-11: raw frames are stored only while someone is in bed and for
TOF_STORE_AFTER_EXIT_SEC after an exit; the diagnostic copy is sent either way.
"""
import importlib
import unittest
from unittest.mock import patch

import dashboard_api
import db


class ToFBranchTests(unittest.TestCase):
    def setUp(self):
        # Avoid demo DB writes and scheduler startup when importing the Flask app.
        with patch.object(dashboard_api, 'register_dashboard'):
            self.server = importlib.import_module('tof_server')
        self.frame = {'sensor': 'tof1', 'resolution': '8x8', 'distances_mm': [500]*64,
                      'raw_distances_mm': [510]*64, 'target_status': [5]*64, 'targets': [1]*64}

    def post(self, in_bed, store_until=0.0):
        server = self.server
        server._raw_store['until'] = store_until
        with patch.object(server, 'update_presence', side_effect=lambda *a: server.presence.update(in_bed=in_bed)), \
             patch.object(db, 'insert_tof', return_value=1) as store, \
             patch.object(server.diagnostic_publisher, 'publish') as publish:
            response = server.app.test_client().post('/tof', json=self.frame)
        self.assertEqual(response.status_code, 200)
        return store, publish

    def test_in_bed_frame_is_stored_and_copied(self):
        store, publish = self.post(in_bed=True)
        self.assertEqual(store.call_args.args[0], 'tof1')
        self.assertEqual(store.call_args.args[3], self.frame['distances_mm'])
        self.assertEqual(publish.call_args.args[0], 'tof1')
        self.assertEqual(publish.call_args.args[1]['raw_distances_mm'], self.frame['raw_distances_mm'])
        self.assertEqual(self.server.latest['tof1']['distances_mm'], self.frame['distances_mm'])

    def test_empty_bed_frame_is_copied_but_not_stored(self):
        store, publish = self.post(in_bed=False)
        store.assert_not_called()
        self.assertEqual(publish.call_args.args[0], 'tof1')

    def test_frames_within_exit_window_are_stored(self):
        import time
        store, _ = self.post(in_bed=False, store_until=time.time() + 5)
        store.assert_called_once()


if __name__ == '__main__':
    unittest.main()
