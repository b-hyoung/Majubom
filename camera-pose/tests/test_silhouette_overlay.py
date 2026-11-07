"""Display alignment/transparency/ephemerality, not real segmentation accuracy."""
import base64
import copy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
import cv2
import numpy as np

from bed_pose.contracts import ModelInput, FramePacket, PoseOutput
from bed_pose.silhouette_overlay import capture_preview, select_preview
from bed_pose.live_recording import verify_live
from test_silhouette import Mask, mask, row_with_mask
import test_silhouette as fixtures


def capture(row, values=None):
    frame=ModelInput(FramePacket('stream',7,np.zeros((360,640,3),dtype=np.uint8),{}),1)
    return capture_preview(SimpleNamespace(segmentation_masks=[values or mask()]),row['raw_candidates'],frame)


class OverlayTests(unittest.TestCase):
    def test_png_foreground_color_transparency_and_native_channel_shape(self):
        row=row_with_mask()
        for values in (mask(), Mask(mask().values[:,:,None])):
            packet=capture(row,values);preview=select_preview(packet,row,640,360)
            raw=base64.b64decode(preview['image'].split(',')[1])
            image=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_UNCHANGED)
            self.assertEqual(image.shape,(360,640,4))
            self.assertEqual(image[180,300].tolist(),[245,110,170,105])
            self.assertEqual(int(image[0,0,3]),0)
            self.assertEqual(preview['stream_frame_id'],7)
            self.assertNotIn('mask_pixels',packet)

    def test_large_mask_is_bounded_and_detached(self):
        row=row_with_mask();values=mask(width=1920,height=1080)
        frame=ModelInput(FramePacket('stream',7,np.zeros((1080,1920,3),dtype=np.uint8),{}),1)
        packet=capture_preview(SimpleNamespace(segmentation_masks=[values]),row['raw_candidates'],frame)
        raw=base64.b64decode(packet['candidates'][0]['image'].split(',')[1])
        image=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_UNCHANGED)
        self.assertEqual(image.shape,(360,640,4))
        values.values[:]=0
        self.assertEqual(base64.b64decode(packet['candidates'][0]['image'].split(',')[1]),raw)

    def test_missing_invalid_and_uninformative_masks_do_not_draw(self):
        row=row_with_mask()
        for value in (None, [], [Mask(np.ones((10,20),dtype=np.float32))],
                      [Mask(np.full((360,640),np.nan,dtype=np.float32))],
                      [Mask(np.zeros((360,640),dtype=np.float32))]):
            frame=ModelInput(FramePacket('stream',7,np.zeros((360,640,3),dtype=np.uint8),{}),1)
            packet=capture_preview(SimpleNamespace(segmentation_masks=value),row['raw_candidates'],frame)
            self.assertIsNone(select_preview(packet,row,640,360))

    def test_selection_frame_and_target_guard_but_not_posture_joint_gate(self):
        row=row_with_mask();packet=capture(row)
        # Display an actual mask even when torso coordinates are insufficient for classification.
        row['posture_assessment'].update(label=None,reasons=['automatic_torso_joints_missing'])
        self.assertIsNotNone(select_preview(packet,row,640,360))
        for field,value in (('stream_frame_id',8),('stream_id','other'),('model_timestamp_ms',2),('width',600)):
            changed=copy.deepcopy(packet);changed[field]=value
            self.assertIsNone(select_preview(changed,row,640,360))
        row['target_tracking']={'status':'held'}
        self.assertIsNone(select_preview(packet,row,640,360))
        row['target_tracking']={'status':'tracking_provisional'}
        row['posture_assessment']['candidate_id']=None
        self.assertIsNone(select_preview(packet,row,640,360))
        row['posture_assessment']['candidate_id']='second'
        packet['candidates'].append(dict(packet['candidates'][0],candidate_id='second',image='data:image/png;base64,second'))
        self.assertEqual(select_preview(packet,row,640,360)['image'],'data:image/png;base64,second')


class OverlayWebTests(unittest.TestCase):
    setUp=fixtures.SilhouetteWebTests.setUp
    cleanup=fixtures.SilhouetteWebTests.cleanup
    request=fixtures.SilhouetteWebTests.request
    wait=fixtures.SilhouetteWebTests.wait
    demo=fixtures.SilhouetteWebTests.demo
    control=fixtures.SilhouetteWebTests.control
    world_input=fixtures.SilhouetteWebTests.world_input
    weighted_input=fixtures.SilhouetteWebTests.weighted_input
    finish=fixtures.SilhouetteWebTests.finish

    def overlay_input(self):
        sid=self.weighted_input();engine=self.workspace.sessions[sid].worker.engine
        self.mask=mask()
        from bed_pose.silhouette import capture as sparse_capture
        def estimate(m):
            raw=copy.deepcopy(self.raw);result=SimpleNamespace(segmentation_masks=None if self.mask is None else [self.mask])
            return PoseOutput(raw,m.model_timestamp_ms,True,sparse_capture(result,raw,m),capture_preview(result,raw,m))
        engine.adapter.estimate=estimate
        self.wait(lambda x:x['sessions'][0].get('silhouette_assessment',{}).get('status')=='consistent')
        return sid

    def test_overlay_in_same_preview_not_recorded_and_missing_clears_it(self):
        sid=self.overlay_input();_,view=self.request('/api/view/'+sid)
        self.assertEqual(view['silhouette_preview']['stream_frame_id'],view['frame_id'])
        self.assertEqual(view['silhouette_preview']['candidate_id'],view['posture_assessment']['candidate_id'])
        self.assertEqual(view['posture_assessment']['label'],'side')
        root=self.finish(sid);self.assertEqual(verify_live(root)['integrity'],'verified')
        rows=[json.loads(line) for line in (root/'live_frames.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all('silhouette_preview' not in row and 'data:image/png' not in json.dumps(row) for row in rows))
        self.mask=None
        self.wait(lambda x:x['sessions'][0]['silhouette_assessment']['reasons']==['mask_not_provided'])
        self.assertIsNone(self.request('/api/view/'+sid)[1]['silhouette_preview'])

    def test_display_packet_failure_does_not_fail_pose_or_change_label(self):
        sid=self.overlay_input();engine=self.workspace.sessions[sid].worker.engine;estimate=engine.adapter.estimate
        def broken(m):return replace(estimate(m),silhouette_preview={'candidates':None})
        before=self.request('/api/state')[1]['sessions'][0]['sequence']
        engine.adapter.estimate=broken
        self.wait(lambda x:x['sessions'][0]['sequence']>before+2)
        _,view=self.request('/api/view/'+sid)
        self.assertIsNone(view['silhouette_preview']);self.assertEqual(view['posture_assessment']['label'],'side')
