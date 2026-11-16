"""Run the saved pre-fall predictor (mmWave/prefall_predictor.py) over recorded scenario trials, frame by frame, as the
reader would live. Also checks that its probabilities equal the batch evaluation code (prefall_final.py) on the same
windows, so the deployed predictor and the reported numbers use the same features.
Usage: python score_recording.py <session_dir> [...]
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from prefall_predictor import PreFallPredictor  # noqa: E402
import prefall_final as F  # noqa: E402
import real_check as R  # noqa: E402


def main():
    pred = PreFallPredictor()
    worst = 0.0
    for ses in sys.argv[1:]:
        for tr in R.load_trials(ses):
            if tr["kind"] == F.SKIP:
                continue
            f = tr["frames"]
            pred.reset(start=0.0)          # trial time 0 = first cue, as in the evaluation
            live = []
            for row in f:
                r = pred.update(*row)
                if r:
                    live.append(r)
            drop = R.drop_start(f, tr["cue_mat"]) if tr["kind"] in F.FALLS else None
            alarms = [r for r in live if r["alarm"] and (drop is None or r["t"] < drop + 1e-6)]
            first = round(alarms[0]["t"], 1) if alarms else None
            note = f"drop at {drop:.1f}s" if drop else "no drop"
            print(f"{os.path.basename(ses)} trial {tr['trial']:2d} {tr['kind']:16s} {note:14s} first alarm at {first}s"
                  + (f" ({drop - first:.1f}s before drop)" if drop and first is not None else ""))
            w = F.trial_windows(tr)
            if w is not None and live:
                batch = pred.model.predict_proba(w[0])[:, 1]
                lp = {round(r["t"], 2): r["prob"] for r in live}
                compared = sum(round(e, 2) in lp for e in w[2])
                if compared < len(w[2]):
                    print(f"   note: {len(w[2]) - compared} batch windows had no live window")
                for e, p in zip(w[2], batch):
                    if round(e, 2) in lp:
                        worst = max(worst, abs(lp[round(e, 2)] - p))
    print(f"max |live - batch| probability difference: {worst:.2e}")


if __name__ == "__main__":
    main()
