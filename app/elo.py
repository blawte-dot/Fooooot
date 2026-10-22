"""Elo بمعايرة تجريبية: فرق التقييم -> احتمالات 1X2 من النتائج الفعلية (8 فئات)، ثم دمج مع Dixon-Coles."""
import bisect
from collections import defaultdict
K, HA, NB = 20.0, 60.0, 8

def _o(gh, ga): return 0 if gh > ga else (1 if gh == ga else 2)

def fit(matches, ref):
    R, n, samples = defaultdict(lambda: 1500.0), defaultdict(int), []
    for d, h, a, gh, ga, *_ in sorted(m for m in matches if m[0] < ref):
        diff = R[h] + HA - R[a]
        if n[h] >= 5 and n[a] >= 5: samples.append((diff, _o(gh, ga)))
        e = 1 / (1 + 10 ** (-diff / 400)); s = (1.0, 0.5, 0.0)[_o(gh, ga)]; g = abs(gh - ga)
        g = 1.0 if g <= 1 else (1.5 if g == 2 else (11 + g) / 8)
        dl = K * g * (s - e); R[h] += dl; R[a] -= dl; n[h] += 1; n[a] += 1
    samples.sort(); out = {"R": dict(R), "edges": None, "tab": None}
    if len(samples) >= 80:
        base = [sum(o == k for _, o in samples) / len(samples) for k in range(3)]; sz = len(samples) // NB
        edges, tab = [samples[i * sz][0] for i in range(1, NB)], []
        for b in range(NB):
            g = samples[b * sz:(b + 1) * sz] if b < NB - 1 else samples[b * sz:]
            tab.append(tuple((sum(o == k for _, o in g) + 5 * base[k]) / (len(g) + 5) for k in range(3)))
        out.update(edges=edges, tab=tab)
    return out

def probs(m, h, a):
    if not m["tab"]: return None
    diff = m["R"].get(h, 1500.0) + HA - m["R"].get(a, 1500.0)
    return m["tab"][bisect.bisect(m["edges"], diff)]

def blend(p1, p2, w, T=1.0):
    q = [w * x + (1 - w) * y for x, y in zip(p1, p2)]
    if T != 1.0: q = [max(v, 1e-9) ** (1 / T) for v in q]
    s = sum(q); return tuple(v / s for v in q)
