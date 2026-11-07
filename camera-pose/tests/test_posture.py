import copy
import unittest
from bed_pose.posture import assess_frame,group_assessment


def frame():
    return {'stream_frame_id':12,'clock_id':'stream:clock','received_ns':'1000',
            'processing_status':'ok','target_tracking':{'status':'not_configured'},
            'mapped_people':[{'candidate_id':'p1','joints':[{'availability':'predicted','x_px':0,'y_px':5}]}],
            'target_posture':'supine','verified_gt':'side','sensor_results':{'label':'edge_sitting'}}

class Classifier:
    model_id='test-only'
    def __init__(self,label):self.label=label;self.seen=None
    def predict(self,observation):self.seen=observation;return self.label

class PostureTests(unittest.TestCase):
    def test_unconfigured_preserves_frame_and_never_uses_goal_as_answer(self):
        row=frame();before=copy.deepcopy(row);a=assess_frame(row,100,100,None)
        self.assertEqual(a['status'],'unavailable');self.assertIsNone(a['label'])
        self.assertEqual(a['reasons'],['classifier_not_configured']);self.assertEqual(a['stream_frame_id'],12)
        self.assertEqual(a['usable_joints'],1);self.assertEqual(row,before)

    def test_no_candidate_ambiguous_and_tracking_loss_hold(self):
        for people,tracking,reason in (([],{'status':'not_configured'},'no_candidate'),
              (frame()['mapped_people']*2,{'status':'not_configured'},'ambiguous_target'),
              (frame()['mapped_people'],{'status':'held'},'target_held')):
            row=frame();row.update(mapped_people=people,target_tracking=tracking)
            self.assertIn(reason,assess_frame(row,100,100,None)['reasons'])

    def test_coordinates_zero_missing_nonfinite_outside(self):
        row=frame();row['mapped_people'][0]['joints']=[{'availability':'predicted','x_px':x,'y_px':5} for x in (0,-1,float('nan'),100,None,True)]
        self.assertEqual(assess_frame(row,100,100,None)['usable_joints'],1)
        row['mapped_people'][0]['joints']=row['mapped_people'][0]['joints'][1:]
        self.assertIn('no_usable_coordinates',assess_frame(row,100,100,None)['reasons'])

    def test_classifier_only_receives_visual_input(self):
        row=frame();model=Classifier('side');a=assess_frame(row,100,100,None,model)
        self.assertEqual((a['status'],a['label']),('predicted','side'))
        self.assertEqual(set(vars(model.seen)),{'points','width','height','bed_rectangle','world_body','world_points','body_state'})
        self.assertIsNone(model.seen.world_body)
        self.assertEqual(model.seen.points,((0,5),));self.assertFalse(a['identity_verified'])

    def test_abstention_invalid_output_failure_and_processing_gate(self):
        for label,reason in ((None,'insufficient_or_out_of_scope'),('unrecognized_posture','invalid_classifier_output'),([], 'invalid_classifier_output')):
            a=assess_frame(frame(),100,100,None,Classifier(label))
            self.assertEqual(a['status'],'held');self.assertIsNone(a['label']);self.assertIn(reason,a['reasons'])
        class Broken(Classifier):
            def predict(self,observation):raise RuntimeError('test')
        self.assertIn('classifier_failed',assess_frame(frame(),100,100,None,Broken(None))['reasons'])
        row=frame();row['processing_status']='failed';model=Classifier('supine')
        self.assertEqual(assess_frame(row,100,100,None,model)['status'],'held');self.assertIsNone(model.seen)

    def test_group_previews_never_treated_as_aligned_prediction(self):
        group=group_assessment([{'input_id':'a','assessment':{'stream_frame_id':1}},
                                {'input_id':'b','assessment':{'stream_frame_id':99}}])
        self.assertEqual(group['status'],'unavailable');self.assertIsNone(group['label'])
        self.assertIn('live_pairing_not_connected',group['reasons'])
