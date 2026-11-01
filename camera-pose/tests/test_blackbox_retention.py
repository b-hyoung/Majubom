"""Retention tests use only temporary synthetic records."""
import json
from pathlib import Path
import unittest
from bed_pose.blackbox_retention import reclaim, _size
from bed_pose.io_utils import atomic_json
import test_live as live


class RetentionTests(unittest.TestCase):
    setUp=live.LiveTests.setUp
    engine=live.LiveTests.engine
    ready=live.LiveTests.ready

    def make_records(self):
        engine=self.ready(self.engine());dirs=[]
        for index in range(3):
            engine.utc_clock=lambda n=index:1700000000000000000+n
            segment={'series_id':'a'*32,'index':0,'previous_measurement_id':None}
            path=engine.start_measurement(blackbox=True,segment=segment)
            engine.step();engine.stop_measurement();dirs.append(path)
        engine.close();return dirs

    def test_oldest_first_and_normal_records_untouched(self):
        dirs=self.make_records();root=self.root/'runs';normal=root/'normal';normal.mkdir();(normal/'data').write_text('keep')
        blackbox=root/'blackbox';total=_size(blackbox);one=_size(dirs[0])
        result=reclaim(root,total-one)
        self.assertEqual(result['deleted'],[dirs[0].name])
        self.assertFalse(dirs[0].exists());self.assertTrue(dirs[1].exists());self.assertTrue(dirs[2].exists())
        self.assertEqual((normal/'data').read_text(),'keep')

    def test_keep_open_group_and_unknown_files_are_protected(self):
        dirs=self.make_records();root=self.root/'runs';blackbox=root/'blackbox'
        (dirs[0]/'.keep').write_text('keep')
        groups=blackbox/'groups'/('b'*32);groups.mkdir(parents=True)
        atomic_json(groups/'manifest.json',{'group_id':groups.name,'members':[{'run_directory':str(dirs[1])}]})
        (dirs[2]/'unrecognized.txt').write_text('keep')
        result=reclaim(root,1,protected=[dirs[0].name])
        self.assertFalse(result['within_limit']);self.assertEqual(result['deleted'],[])
        self.assertTrue(all(p.exists() for p in dirs))

    def test_incomplete_group_or_link_prevents_all_deletion(self):
        dirs=self.make_records();root=self.root/'runs';blackbox=root/'blackbox'
        group=blackbox/'groups'/('b'*32);group.mkdir(parents=True)
        (group/'manifest.json').write_text('{')
        with self.assertRaises(ValueError):reclaim(root,1)
        self.assertTrue(all(p.exists() for p in dirs))
        (group/'manifest.json').unlink();group.rmdir()
        try:(blackbox/'link').symlink_to(self.root,target_is_directory=True)
        except (OSError,NotImplementedError):self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):reclaim(root,1)
        self.assertTrue(all(p.exists() for p in dirs))
