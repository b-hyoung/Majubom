"""The extra ToF snapshot branch must retain the original DB call."""
import importlib
import unittest
from unittest.mock import patch

import dashboard_api
import db


class ToFBranchTests(unittest.TestCase):
    def test_original_storage_and_diagnostic_copy(self):
        # Avoid demo DB writes and scheduler startup when importing the Flask app.
        with patch.object(dashboard_api, 'register_dashboard'):
            server = importlib.import_module('tof_server')
        frame = {'sensor': 'tof1', 'resolution': '8x8', 'distances_mm': [500]*64,
                 'raw_distances_mm': [510]*64, 'target_status': [5]*64, 'targets': [1]*64}
        with patch.object(db, 'insert_tof', return_value=1) as store, patch.object(server.diagnostic_publisher, 'publish') as publish:
            response = server.app.test_client().post('/tof', json=frame)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(store.call_args.args[0], 'tof1')
        self.assertEqual(store.call_args.args[3], frame['distances_mm'])
        self.assertEqual(publish.call_args.args[0], 'tof1')
        self.assertEqual(publish.call_args.args[1]['raw_distances_mm'], frame['raw_distances_mm'])
        self.assertEqual(server.latest['tof1']['distances_mm'], frame['distances_mm'])


if __name__ == '__main__':
    unittest.main()
