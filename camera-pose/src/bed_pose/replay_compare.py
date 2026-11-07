"""Tolerance only for recomputed floating point values; structure/labels stay exact."""
import math


def equivalent(a, b):
    if type(a) is not type(b):return False
    if isinstance(a, float):
        return math.isfinite(a) and math.isfinite(b) and math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12)
    if isinstance(a, dict):return a.keys()==b.keys() and all(equivalent(a[k],b[k]) for k in a)
    if isinstance(a, list):return len(a)==len(b) and all(equivalent(x,y) for x,y in zip(a,b))
    return a==b
