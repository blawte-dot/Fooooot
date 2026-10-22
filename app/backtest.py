"""اختبار walk-forward وضبط ذاتي: كل توقع يُحسب بنموذج دُرّب على ما قبل تاريخ المباراة فقط."""
import math
from datetime import timedelta
from . import model, elo

VARIANTS = {"poisson": dict(xi=0.0, use_dc=False), "poisson_decay": dict(xi=0.0019, use_dc=False),
            "dixon_coles_decay": dict(xi=0.0019, use_dc=True)}

def _outcome(gh, ga): return 0 if gh > ga else (1 if gh == ga else 2)

def _score(preds):
    n = len(preds)
    if not n: return None
    brier = sum(sum((p[k] - (k == p[3])) ** 2 for k in range(3)) for p in preds) / n
    ll = -sum(math.log(max(p[p[3]], 1e-12)) for p in preds) / n
    acc = sum(max(range(3), key=lambda k: p[k]) == p[3] for p in preds) / n
    return {"n": n, "brier": round(brier, 4), "log_loss": round(ll, 4), "accuracy": round(acc * 100, 1)}

def calibration(preds, bins=5):
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        g = [p for p in preds if lo <= p[0] < hi or (b == bins - 1 and p[0] == 1)]
        if g: out.append({"range": f"{int(lo*100)}-{int(hi*100)}%", "n": len(g),
                          "predicted": round(sum(p[0] for p in g) / len(g) * 100, 1),
                          "actual": round(sum(p[3] == 0 for p in g) / len(g) * 100, 1)})
    return out

def _walk(hist, configs, test_days, refit_days, min_train):
    hist = sorted(hist); last = hist[-1][0]; cur = last - timedelta(days=test_days)
    preds = {k: [] for k in configs}; preds["__elo"] = []; base = []; first = None
    while cur <= last:
        end = cur + timedelta(days=refit_days)
        train = [h for h in hist if h[0] < cur]; test = [h for h in hist if cur <= h[0] < end]
        if len(train) >= min_train and test:
            fr = [sum(_outcome(h[3], h[4]) == k for h in train) / len(train) for k in range(3)]
            fitted = {k: model.fit(train, cur, **kw) for k, kw in configs.items()}; el = elo.fit(train, cur)
            for d, h, a, gh, ga, *_ in test:
                try:
                    res = {}
                    for k, m in fitted.items():
                        s = model.predict(m, h, a); res[k] = (s["home"], s["draw"], s["away"])
                except ValueError:
                    continue
                o = _outcome(gh, ga)
                for k, v in res.items(): preds[k].append((*v, o))
                preds["__elo"].append((*(elo.probs(el, h, a) or tuple(fr)), o))
                base.append((*fr, o)); first = first or d
        cur = end
    return preds, base, first, last

def _bin(v):
    n = len(v)
    if not n: return None
    br = lambda i: round(sum((x[i] - x[2]) ** 2 for x in v) / n, 4)
    ll = lambda i: round(-sum(math.log(min(max(x[i] if x[2] else 1 - x[i], 1e-9), 1)) for x in v) / n, 4)
    cal = []
    for b in range(5):
        g = [x for x in v if b / 5 <= x[0] < (b + 1) / 5 or (b == 4 and x[0] == 1)]
        if g: cal.append({"range": f"{b*20}-{b*20+20}%", "n": len(g), "predicted": round(sum(x[0] for x in g) / len(g) * 100, 1),
                          "actual": round(sum(x[2] for x in g) / len(g) * 100, 1)})
    return {"n": n, "brier": br(0), "log_loss": ll(0), "baseline_brier": br(1), "baseline_log_loss": ll(1), "calibration": cal}

def market_eval(hist, test_days=240, refit_days=30, min_train=150, **kw):
    """اختبار أسواق الأهداف (أكثر من 2.5، كلا الفريقين يسجل) مقابل نسبة تاريخية بسيطة."""
    hist = sorted(hist); last = hist[-1][0]; cur = last - timedelta(days=test_days); out = {"over25": [], "btts": []}
    while cur <= last:
        end = cur + timedelta(days=refit_days)
        train = [h for h in hist if h[0] < cur]; test = [h for h in hist if cur <= h[0] < end]
        if len(train) >= min_train and test:
            m = model.fit(train, cur, **kw)
            fo = sum(1 for h in train if h[3] + h[4] > 2) / len(train); fb = sum(1 for h in train if h[3] > 0 and h[4] > 0) / len(train)
            for d, h, a, gh, ga, *_ in test:
                try: lh, la = model.lambdas(m, h, a)
                except ValueError: continue
                mat = model.score_matrix(lh, la, m["rho"]); n = len(mat)
                out["over25"].append((sum(mat[i][j] for i in range(n) for j in range(n) if i + j > 2), fo, int(gh + ga > 2)))
                out["btts"].append((sum(mat[i][j] for i in range(1, n) for j in range(1, n)), fb, int(gh > 0 and ga > 0)))
        cur = end
    return {k: _bin(v) for k, v in out.items()}

def run(hist, test_days=240, refit_days=30, min_train=150):
    configs = {k: dict(v) for k, v in VARIANTS.items()}
    preds, base, first, last = _walk(hist, configs, test_days, refit_days, min_train)
    return {"period": [first.isoformat() if first else None, last.isoformat()], "tested_matches": len(base),
            "models": {**{k: _score(v) for k, v in preds.items() if k != "__elo"}, "elo": _score(preds["__elo"]),
                       "dc+elo (70/30)": _score([(*elo.blend(p[:3], e[:3], 0.7), p[3]) for p, e in zip(preds["dixon_coles_decay"], preds["__elo"])]),
                       "baseline": _score(base)},
            "calibration_dixon_coles_decay": calibration(preds["dixon_coles_decay"]),
            "goals_markets": market_eval(hist, test_days, refit_days, min_train, xi=0.0019, use_dc=True)}

def tune(hist, test_days=240, refit_days=60, min_train=150, xg=None):
    """التعلم من الأخطاء: يختار معاملات النموذج التي تقلل خطأ LogLoss على مباريات لم يرها قبل التوقع."""
    configs = {f"{xi}|{sh}": dict(xi=xi, shrink=sh, use_dc=True, iters=60)       # 6 تركيبات بدل 9: الخادم المجاني ضعيف المعالج
               for xi in (0.0008, 0.0019, 0.004) for sh in (2.0, 4.0)}
    preds, base, _, _ = _walk(hist, configs, test_days, refit_days, min_train)
    sc = {k: _score(v) for k, v in preds.items() if v and k != "__elo"}
    if not sc or next(iter(sc.values()))["n"] < 100: return None
    best = min(sc, key=lambda k: sc[k]["log_loss"]); xi, sh = map(float, best.split("|"))
    P, E, alpha, xg_gain = preds[best], preds["__elo"], 0.0, 0.0
    if xg:   # هل يحسّن xG الخطأ؟ يُعتمد فقط بتحسن ملموس
        c2 = {f"a{a}": dict(xi=xi, shrink=sh, use_dc=True, iters=80, xg=xg, alpha=a) for a in (0.0, 0.5, 1.0)}
        p2, _, _, _ = _walk(hist, c2, test_days, refit_days, min_train)
        s2 = {k: _score(v) for k, v in p2.items() if v and k != "__elo"}
        if s2:
            bk = min(s2, key=lambda k: s2[k]["log_loss"]); P, E = p2["a0.0"], p2["__elo"]
            if bk != "a0.0" and s2[bk]["log_loss"] < s2["a0.0"]["log_loss"] - 0.0005:
                alpha, P, xg_gain = float(bk[1:]), p2[bk], round(s2["a0.0"]["log_loss"] - s2[bk]["log_loss"], 4)
    dc_ll = _score(P)["log_loss"]; bw, bT, bl = 1.0, 1.0, dc_ll
    for w in (1.0, 0.85, 0.7, 0.55):
        for T in (0.9, 1.0, 1.1, 1.2):
            l = _score([(*elo.blend(p[:3], e[:3], w, T), p[3]) for p, e in zip(P, E)])["log_loss"]
            if l < bl - 0.0005: bw, bT, bl = w, T, l   # لا يُعتمد الدمج إلا بتحسن ملموس
    return {"xi": xi, "shrink": sh, "alpha": alpha, "xg_gain": xg_gain, "w": bw, "T": bT, "log_loss": bl, "n": sc[best]["n"], "dc_log_loss": dc_ll,
            "default_log_loss": (sc.get("0.0019|2.0") or {}).get("log_loss"), "baseline_log_loss": _score(base)["log_loss"]}
