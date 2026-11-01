"""No live servers, model files, API calls or production writes."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .data import ARTIFACTS, ROOT, pair_frames, purged_split, readonly_db, weak_exit_labels
from .models import ContextualProfile, RandomInterceptLMM

sys.path.insert(0, str(ROOT / "server"))
from maintenance_ai import AdaptiveWindow, MaintenanceMonitor
from retrieval import fuse_rankings, sparse_scores
import ai_agent
import paper_kb


class LabelTests(unittest.TestCase):
    def sequence(self, labels, confidence=.95):
        return [{"ts": i*1000, "tof_posture": label, "tof_conf": confidence} for i,label in enumerate(labels)]

    def test_sustained_transition_and_right_censor(self):
        labels = weak_exit_labels(self.sequence(["supine"]*5+["empty"]*5))
        self.assertEqual(labels[2]["target"], 1)
        self.assertEqual(labels[2]["evidence_end"], 7)
        self.assertEqual(labels[0]["target"], 0)
        ending = weak_exit_labels(self.sequence(["supine"]*4))
        self.assertIsNone(ending[-1]["target"])
        self.assertEqual(ending[-1]["reason"], "right_censored")

    def test_flicker_is_not_positive_and_low_confidence_abstains(self):
        labels = weak_exit_labels(self.sequence(["supine"]*3+["empty"]+["supine"]*6))
        self.assertIsNone(labels[0]["target"])
        labels = weak_exit_labels(self.sequence(["supine"]*5+["empty"]*5, confidence=.3))
        self.assertTrue(all(row["target"] is None for row in labels))

    def test_gap_and_unsorted_rejected(self):
        rows = self.sequence(["supine"]*6)
        rows[3]["ts"] = 5000
        with self.assertRaises(ValueError):
            weak_exit_labels(rows)
        rows = self.sequence(["supine"]*6)
        for row in rows[3:]:
            row["ts"] += 10000
        self.assertEqual(weak_exit_labels(rows)[0]["reason"], "time_gap")

    def test_pairing_does_not_zip_far_apart_or_reuse_rows(self):
        def row(sensor,t):
            return {"t": "2026-09-18T00:00:"+t, "sensor": sensor, "resolution":"8x8", "label":"supine"}
        pairs = pair_frames([row("tof1","00.000"),row("tof1","00.100"),row("tof2","00.150"),row("tof2","10.000")])
        self.assertEqual(len(pairs),1)

    def test_future_horizon_is_purged_from_split(self):
        rows = [{"feature_start":float(i), "evidence_end":float(i+6)} for i in range(100)]
        split = purged_split(rows)
        self.assertGreater(len(split["purged"]), 0)
        self.assertLess(max(rows[i]["evidence_end"] for i in split["train"]),
                        min(rows[i]["feature_start"] for i in split["calibration"]))
        self.assertLess(max(rows[i]["evidence_end"] for i in split["calibration"]),
                        min(rows[i]["feature_start"] for i in split["test"]))

    def test_sqlite_reader_cannot_write(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"db.sqlite"
            c=sqlite3.connect(p); c.execute("CREATE TABLE t(x)"); c.commit(); c.close()
            c=readonly_db(p)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    c.execute("INSERT INTO t VALUES(1)")
            finally:
                c.close()


class DetectorTests(unittest.TestCase):
    def test_context_profile_exact_distance_and_abstention(self):
        x = np.array([[[0.],[1.]], [[2.],[3.]]])
        m=ContextualProfile().fit(x,["occupied"]*2)
        query=np.array([[1.],[1.]])
        expected=min(np.sqrt(np.mean((((ref-m.mean)/m.scale)-((query-m.mean)/m.scale))**2)) for ref in x)
        self.assertAlmostEqual(m.distance(query,"occupied"),expected)
        self.assertEqual(m.predict(query,"empty")["status"],"insufficient_reference")
        self.assertEqual(m.predict(query,"occupied")["status"],"insufficient_reference")
        m.calibrate([query, query+.1],["occupied"]*2)
        self.assertIsNotNone(m.predict(query,"occupied")["threshold"])
        self.assertIsNone(m.distance([[float('nan')],[0]],"occupied"))

    def test_adwin_constant_vs_shift(self):
        monitor=AdaptiveWindow()
        self.assertFalse(any(monitor.update(.2)["drift"] for _ in range(500)))
        alarms=[i for i in range(100) if monitor.update(.8)["drift"]]
        self.assertTrue(alarms)
        self.assertLess(alarms[0],50)
        self.assertLessEqual(len(monitor.values),256)

    def test_adwin_rejects_invalid(self):
        for value in (float("nan"),float("inf"),-.1,1.1):
            with self.assertRaises(ValueError): AdaptiveWindow().update(value)

    def test_maintenance_isolation_quality_duplicates_gap(self):
        monitor=MaintenanceMonitor({"speed":(0,3)},max_gap_sec=10)
        self.assertEqual(monitor.observe("bed_sim_1","mmw","speed",1,0)["reason"],"simulation")
        self.assertEqual(monitor.observe("a","mmw","speed",1,0,held=True)["status"],"skipped")
        self.assertEqual(monitor.observe("a","mmw","speed",1,0)["width"],1)
        self.assertEqual(monitor.observe("a","mmw","speed",1,0)["reason"],"duplicate_or_out_of_order")
        self.assertEqual(monitor.observe("b","mmw","speed",1,1)["width"],1)
        result=monitor.observe("a","mmw","speed",1,30)
        self.assertTrue(result["reset_after_gap"])
        self.assertEqual(result["width"],1)
        self.assertFalse(result["automatic_baseline_update"])

    def test_lmm_requires_real_group_identifiers(self):
        with self.assertRaises(ValueError): RandomInterceptLMM().fit([1,2,3],[None]*3)
        with self.assertRaises(ValueError): RandomInterceptLMM().fit([1,2,3],["same"]*3)

    def test_lmm_partial_pooling_and_uncertainty(self):
        rng=np.random.default_rng(10)
        values=np.concatenate([rng.normal(mean,.1,20) for mean in (1,3,5,7)])
        m=RandomInterceptLMM().fit(values,np.repeat(np.arange(4),20))
        cold=m.score(2,[]); warm=m.score(2,[1.]*10)
        self.assertGreater(warm["expected"],1)
        self.assertLess(warm["expected"],cold["expected"])
        self.assertLess(warm["predictive_sd"],cold["predictive_sd"])


class RetrievalTests(unittest.TestCase):
    def test_no_lexical_match_keeps_dense_order(self):
        order,_,_,sr=fuse_rankings(["c","b","a"],{"a":0,"b":-1,"c":0})
        self.assertEqual(order,["c","b","a"])
        self.assertEqual(sr,{})
        self.assertEqual(sparse_scores("x",["",""]),[0.,0.])

    def test_lexical_ties_same_weight(self):
        _,scores,_,sr=fuse_rankings([],{"b":2,"a":2,"c":1})
        self.assertEqual(scores["a"],scores["b"])
        self.assertEqual(sr,{"a":1,"b":1,"c":3})

    def test_agent_uses_hybrid_and_keeps_dense_comparison(self):
        hit={"chunk_id":"p:s","title":"test"}
        with patch.object(ai_agent,"gather_patient_state",return_value={}), \
             patch.object(ai_agent,"generate_queries",return_value={"success":True,"queries":["test"]}), \
             patch.object(ai_agent,"judge_sufficiency",return_value={"success":True,"sufficient":True}), \
             patch.object(paper_kb,"search_hybrid",return_value={"success":True,"results":[hit]}) as hybrid, \
             patch.object(paper_kb,"search",return_value={"success":True,"results":[hit]}) as dense:
            result=ai_agent.investigate("test")
            self.assertTrue(result["success"])
            hybrid.assert_called_once()
            dense.assert_not_called()
            self.assertEqual(result["search_method"],"hybrid")
            ai_agent.investigate("test",search_method="dense")
            dense.assert_called_once()

    def test_agent_propagates_search_failure_without_inventing_evidence(self):
        with patch.object(ai_agent,"gather_patient_state",return_value={}), \
             patch.object(ai_agent,"generate_queries",return_value={"success":True,"queries":["test"]}), \
             patch.object(paper_kb,"search_hybrid",return_value={"success":False,"error_type":"no_index"}):
            result=ai_agent.investigate("test")
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"],"no_index")
        self.assertNotIn("final_results",result)

    def test_hybrid_uses_real_index_with_cached_query_without_network(self):
        cache=ARTIFACTS/"query_embeddings.npz"
        if not cache.exists(): self.skipTest("optional local embedding cache absent")
        with np.load(cache,allow_pickle=False) as data:
            vector=data["vectors"][0].tolist()
        q=json.loads((Path(__file__).parent/"rag_qrels.json").read_text(encoding="utf-8"))["queries"][0]["query"]
        with patch.object(paper_kb,"embed_texts",return_value={"success":True,"embeddings":[vector]}):
            result=paper_kb.search_hybrid(q)
        self.assertTrue(result["success"])
        self.assertEqual(result["results"][0]["paper_id"],"dementia-01")
        self.assertEqual(result["rank_base"],1)


if __name__ == "__main__":
    unittest.main()
