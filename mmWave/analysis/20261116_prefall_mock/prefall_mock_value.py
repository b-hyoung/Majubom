"""How much does the mock data help? (2026-11-17, analysis only)

Same model as the deployed v3 (15 features, fixed reference walking, 8 s horizon, mean of last 3 windows). Training sets:
  real_only   = the other person's real scenario trials + real walking (no mock)
  mock_only   = mock episodes only (generated without the left-out person's sessions)
  real+mock   = both, as deployed (mock share 0/10/25/50/100% to see the trend)
Tested on each real person left out (lee, kim; pooled 23 falls, 24 controls) and on the mock test set.
Because the models' scores sit at different levels, they are compared at many thresholds (0.20-0.80):
  at the deployed 0.50, and at the threshold that catches the most falls with at most 6/24 control trials alarming
  (the deployed v3 at 0.50 has 6/24).
Usage: python prefall_mock_value.py   → prefall_mock_value_result.json
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prefall_cross_person as X  # noqa: E402
import prefall_final as F  # noqa: E402
import prefall_lead as L  # noqa: E402
import prefall_popbase as B  # noqa: E402
import prefall_threshold_sweep as S  # noqa: E402
import prefall_v3_path as V  # noqa: E402

SEL = slice(0, 15)
FRACS = [0.0, 0.1, 0.25, 0.5, 1.0]
CTRL_MAX = 6


def train(mock, real, W):
    if not mock:  # real only: plain fit on real trials + walking
        Xr = np.vstack([e["X"] for e in real] + [W])
        yr = np.concatenate([L.labels(e, ("h", 8.0)) for e in real] + [np.zeros(len(W), int)])
        return L.I.make("xgboost").fit(Xr, yr)
    if not real:
        Xm = np.vstack([e["X"] for e in mock]); ym = np.concatenate([L.labels(e, ("h", 8.0)) for e in mock])
        return L.I.make("xgboost").fit(Xm, ym)
    return L.train(mock, real, W, ("h", 8.0))


def subset(eps, frac, seed):
    if frac >= 1.0:
        return eps
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(eps), int(round(frac * len(eps))), replace=False)
    return [eps[i] for i in sorted(idx)]


def summarise(rows):
    at = next(r for r in rows if abs(r["threshold"] - 0.5) < 1e-9)
    ok = [r for r in rows if r["real_control_alarm"] <= CTRL_MAX]
    best = max(ok, key=lambda r: (r["real_caught"], -r["threshold"])) if ok else None
    return {"at_0.50": at, f"best_with_controls_le_{CTRL_MAX}": best}


def main():
    ss = X.scenario_sessions()
    per = {}
    for p in sorted(ss):
        refp = B.reference(exclude=p)
        per[p] = (V.sub(V.mock(f"mock_lead_not_{p}.pkl", refp, f"not{p}") + V.mock(f"mock_exit_not_{p}.pkl", refp, f"not{p}"), SEL),
                  V.sub(V.real([s for q, v in ss.items() if q != p for s in v], refp), SEL),
                  V.walk_X(refp, p)[:, SEL], V.sub(V.real(ss[p], refp), SEL))
    ref = B.reference()
    te = V.sub(V.mock("mock_lead_test.pkl", ref, "all") + V.mock("mock_exit_test.pkl", ref, "all"), SEL)
    tr = V.sub(V.mock("mock_lead_train.pkl", ref, "all") + V.mock("mock_exit_train.pkl", ref, "all"), SEL)
    R_all = V.sub(V.real([s for v in ss.values() for s in v], ref), SEL)
    W_all = V.walk_X(ref)[:, SEL]

    setups = {"real_only": (0.0, True), "mock_only": (1.0, False)}
    setups.update({f"real+mock_{int(f * 100)}pct": (f, True) for f in FRACS[1:]})
    result = {"control_limit": CTRL_MAX, "setups": {}}
    for name, (frac, use_real) in setups.items():
        real_eps, real_p = [], []
        for i, (p, (m_not, r_other, w_not, own)) in enumerate(per.items()):
            m = subset(m_not, frac, 100 + i) if frac > 0 else []
            model = train(m, r_other if use_real else [], w_not)
            real_eps += own
            real_p += [model.predict_proba(e["X"])[:, 1] for e in own]
        full = train(subset(tr, frac, 7) if frac > 0 else [], R_all if use_real else [], W_all)
        mock_p = [full.predict_proba(e["X"])[:, 1] for e in te]
        rows = []
        for thr in S.THRESHOLDS:
            c, f, ca, cn, leads, _ = S.counts(real_eps, real_p, thr)
            mc, mf, mca, mcn, mleads, mfar = S.counts(te, mock_p, thr)
            rows.append({"threshold": thr, "real_caught": c, "real_falls": f, "real_control_alarm": ca, "real_controls": cn,
                         "real_mean_lead": round(float(np.mean(leads)), 2) if leads else None,
                         "mock_caught_pct": round(100 * mc / mf, 1), "mock_false_alarms_per_h": round(mfar, 2),
                         "mock_mean_lead": round(float(np.mean(mleads)), 2) if mleads else None})
        s = summarise(rows)
        result["setups"][name] = {"mock_episodes_per_left_out_model": [len(subset(v[0], frac, 0)) if frac > 0 else 0 for v in per.values()],
                                  **s, "rows": rows}
        a, b = s["at_0.50"], s[f"best_with_controls_le_{CTRL_MAX}"]
        print(f"{name:18s} @0.50: real {a['real_caught']}/{a['real_falls']} ctrl {a['real_control_alarm']}/24 lead {a['real_mean_lead']} | "
              f"mock {a['mock_caught_pct']}% FA/h {a['mock_false_alarms_per_h']} || best ctrl<={CTRL_MAX}: "
              + (f"thr {b['threshold']} real {b['real_caught']}/{b['real_falls']} ctrl {b['real_control_alarm']} lead {b['real_mean_lead']}" if b else "none"),
              flush=True)
        json.dump(result, open(os.path.join(HERE, "prefall_mock_value_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
