import unittest
from bed_pose.time_alignment import fifo_pairs



class AlignmentTests(unittest.TestCase):
    def test_tolerance_signed_delta_and_unique_coverage(self):
        a=[{'time_ns':t,'id':i} for i,t in enumerate([100,200,300,500])]
        b=[{'time_ns':t,'id':i} for i,t in enumerate([105,190,400])]
        rows=list(fifo_pairs(a,b,10))
        self.assertEqual([r['delta_ns'] for r in rows if r['status']=='matched'],['5','-10'])
        self.assertEqual(sorted(r['left']['id'] for r in rows if r['left']),[0,1,2,3])
        self.assertEqual(sorted(r['right']['id'] for r in rows if r['right']),[0,1,2])
        self.assertEqual(sum(r['status']=='unmatched' for r in rows),3)

    def test_empty_zero_tolerance_and_out_of_order(self):
        self.assertEqual(list(fifo_pairs([],[],0)),[])
        self.assertEqual(len(list(fifo_pairs([{'time_ns':1}],[],0))),1)
        self.assertEqual(list(fifo_pairs([{'time_ns':1}],[{'time_ns':1}],0))[0]['status'],'matched')
        with self.assertRaises(ValueError):list(fifo_pairs([{'time_ns':2},{'time_ns':1}],[],1))
        with self.assertRaises(ValueError):list(fifo_pairs([],[],True))
