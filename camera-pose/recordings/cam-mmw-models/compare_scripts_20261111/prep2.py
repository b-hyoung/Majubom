"""More time-series preprocessing (not compared before), same setup: 9 recordings, HGB, leave-one-recording-out."""
import sys, json, time
OUT = sys.argv[1]; sys.argv = ["x", "unused.json"]
src = open("mmw_compare.py", encoding="utf-8").read().split("res = {")[0]
exec(src)
import pywt
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import savgol_coeffs
g = data.groupby("seg", sort=False)
SG = savgol_coeffs(W, 2, pos=W - 1, use="dot")          # causal: fit last 1 s, evaluate at newest frame
def sg_causal(s):
    x = s.to_numpy(float); y = x.copy()
    if len(x) >= W:
        win = sliding_window_view(x, W); y[W - 1:] = win @ SG   # NaN in window -> NaN
    return pd.Series(y, index=s.index)
WW = 32                                                   # ~1.8 s trailing window for wavelet
def wav_causal(s):
    x = s.to_numpy(float); y = x.copy()
    for t in range(WW - 1, len(x)):
        seg = x[t - WW + 1:t + 1]
        if np.isnan(seg).any(): y[t] = np.nan; continue
        c = pywt.wavedec(seg, "sym4", level=2, mode="periodization")
        sig = np.median(np.abs(c[-1])) / 0.6745; thr = sig * np.sqrt(2 * np.log(WW))
        c = [c[0]] + [pywt.threshold(d, thr, mode="soft") for d in c[1:]]
        y[t] = pywt.waverec(c, "sym4", mode="periodization")[-1]
    return pd.Series(y, index=s.index)
locf = lambda s: s.ffill()
lin = lambda s: s.interpolate(method="linear", limit_area="inside")
def make(name):
    f = pd.DataFrame(index=data.index)
    for c in RAW:
        if name.startswith("P7"): f[c] = g[c].transform(locf)
        elif name.startswith("P8"): f[c] = g[c].transform(lin)
        elif name.startswith("P9"): f[c] = g[c].transform(lambda s: sg_causal(locf(s)))
        elif name.startswith("P10"): f[c] = g[c].transform(lambda s: wav_causal(locf(s)))
        elif name.startswith("P11"): f[c] = g[c].transform(lambda s: lin(s).ewm(alpha=0.1, ignore_na=True).mean())
    return f
ref = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, l2_regularization=1.0, class_weight="balanced", random_state=0)
res = {}
for name in ["P7 LOCF (앞 값 채우기)", "P8 선형 보간", "P9 Savitzky-Golay (1초, 2차, 과거 값만)", "P10 웨이블릿 잡음 제거 (1.8초, sym4)", "P11 선형 보간 + EMA"]:
    t = time.time(); X = make(name); res[name] = loro(X, "HGB", ref)
    print(name, round(res[name]["acc"], 4), round(res[name]["f1"], 4), f"{time.time()-t:.0f}s", flush=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
