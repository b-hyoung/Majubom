"""Image hints must remain read-only and must not imply pose/occupancy evidence."""
import unittest
import numpy as np
from bed_pose.camera_diagnostics import image_diagnostics


class CameraDiagnosticTests(unittest.TestCase):
    def test_dark_clipped_and_middle_scenes_are_distinguished(self):
        dark=image_diagnostics(np.zeros((480,640,3),np.uint8))
        white=image_diagnostics(np.full((480,640,3),255,np.uint8))
        middle=image_diagnostics(np.full((480,640,3),120,np.uint8))
        self.assertTrue(dark['low_light']);self.assertFalse(dark['highlight_clipping'])
        self.assertFalse(white['low_light']);self.assertTrue(white['highlight_clipping'])
        self.assertFalse(middle['low_light']);self.assertFalse(middle['highlight_clipping'])
        self.assertTrue(dark['diagnostic_only']);self.assertNotIn('person_present',dark)

    def test_read_only_unusual_geometry_and_rgb_channel_order(self):
        rgb=np.array([[[255,0,0],[0,0,255]]],dtype=np.uint8);copy=rgb.copy();rgb.setflags(write=False)
        result=image_diagnostics(rgb)
        np.testing.assert_array_equal(rgb,copy)
        self.assertGreater(image_diagnostics(copy[:,:1])['mean_luma_0_255'],image_diagnostics(copy[:,1:])['mean_luma_0_255'])
        self.assertTrue(np.isfinite(result['mean_luma_0_255']))
