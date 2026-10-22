"""محرك الأسواق الرقمية: تسديدات على المرمى، تسديدات، بطاقات، ركنيات، أخطاء.
نموذج هجوم/دفاع (مثل الأهداف) + توزيع ذي الحدين السالب لمعالجة التشتت، واختبار walk-forward لكل سوق مقابل المتوسط التاريخي."""
import math
from datetime import date, timedelta
from . import model, xg as xgm, backtest, cache

METRICS = {"sot": ("التسديدات على المرمى", [5.5, 6.5, 7.5, 8.5, 9.5, 10.5], [2.5, 3.5, 4.5, 5.5]),
           "shots": ("التسديدات", [20.5, 22.5, 24.5, 26.5, 28.5], [9.5, 11.5, 13.5]),
           "yellow": ("البطاقات الصفراء", [2.5, 3.5, 4.5, 5.5], [0.5, 1.5, 2.5]),
           "corners": ("الركنيات", [8.5, 9.5, 10.5, 11.5], [3.5, 4.5, 5.5, 6.5]),
           "corners_1h": ("الركنيات - الشوط الأول", [3.5, 4.5, 5.5], [1.5, 2.5, 3.5]),
           "corners_2h": ("الركنيات - الشوط الثاني", [4.5, 5.5, 6.5], [1.5, 2.5, 3.5]),
           "fouls": ("الأخطاء", [20.5, 23.5, 26.5], [9.5, 11.5, 13.5])}
MIN_ROWS, MIN_TEAM = 120, 8
_CM = cache.TTLCache(80)

def _model(rows, ref, key):
    eff = min(ref, date.today() + timedelta(days=1))   # النموذج نفسه لكل المباريات القادمة في الدوري
    sig = (key, eff, len(rows), max(r[0] for r in rows), min(r[0] for r in rows), rows[0][5], rows[-1][5])
    return _CM.get_or(sig, 1800, lambda: fit(rows, eff))

def _pmf(k, mu, phi):
    if mu <= 0: return 1.0 if k == 0 else 0.0
    if phi < 1e-4: return math.exp(-mu + k * math.log(mu) - math.lgamma(k + 1))
    r = 1 / phi; p = r / (r + mu)
    return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p))

def p_over(mu, phi, line): return max(0.0, min(1.0, 1 - sum(_pmf(k, mu, phi) for k in range(int(line) + 1))))

def rows_for(hist, key, ref):
    out = []
    for h in hist:
        if len(h) < 6 or h[0] >= ref: continue
        st = xgm.STATS.get(h[5])
        if st and key in st: out.append((h[0], h[1], h[2], st[key][0], st[key][1], h[5]))
    return out

def fit(rows, ref):
    m = model.fit(rows, ref, use_dc=False, iters=100); num = den = 0.0
    for d, h, a, vh, va, *_ in rows:
        if d >= ref: continue
        lh, la = m["att"][h] * m["def"][a] * m["home_adv"], m["att"][a] * m["def"][h]
        num += (vh - lh) ** 2 - lh + (va - la) ** 2 - la; den += lh * lh + la * la
    m["phi"] = max(0.0, num / den) if den else 0.0
    return m

def expect(m, h, a):
    c = m["counts"]
    if c.get(h, 0) < MIN_TEAM or c.get(a, 0) < MIN_TEAM: raise ValueError("insufficient")
    return m["att"][h] * m["def"][a] * m["home_adv"], m["att"][a] * m["def"][h]

def total_dist(lh, la, phi):
    mu = lh + la; var = lh + phi * lh * lh + la + phi * la * la
    return mu, max(0.0, (var - mu) / (mu * mu))

def race(lh, la, phi, kmax=45):
    """احتمال أن يتفوق الفريق صاحب الأرض / تعادل / يتفوق الضيف في العدد (افتراض استقلال الفريقين)."""
    px, py = [_pmf(k, lh, phi) for k in range(kmax + 1)], [_pmf(k, la, phi) for k in range(kmax + 1)]
    pw = pd = pl = 0.0; cy = 0.0
    for k in range(kmax + 1):
        pw += px[k] * cy; pd += px[k] * py[k]; cy += py[k]
    cx = 0.0
    for k in range(kmax + 1):
        pl += py[k] * cx; cx += px[k]
    s = pw + pd + pl
    return pw / s, pd / s, pl / s

def half_shares(hist, ref, hid, aid):
    """نسبة ركنيات الشوط الأول من مجموع الشوطين (تاريخيًا): عامة ولكل فريق."""
    r1 = {r[5]: r for r in rows_for(hist, "corners_1h", ref)}; r2 = {r[5]: r for r in rows_for(hist, "corners_2h", ref)}
    both = [m for m in r1 if m in r2]
    if len(both) < 30: return None
    tot = lambda ms: (sum(r1[m][3] + r1[m][4] for m in ms), sum(r2[m][3] + r2[m][4] for m in ms))
    pct = lambda t: round(100 * t[0] / (t[0] + t[1]), 1) if t[0] + t[1] else None
    team = lambda t: [m for m in both if t in (r1[m][1], r1[m][2])]
    return {"n": len(both), "league": pct(tot(both)), "home": pct(tot(team(hid))), "away": pct(tot(team(aid))), "home_n": len(team(hid)), "away_n": len(team(aid))}

def predict(hist, key, hid, aid, ref):
    label, tl, ml = METRICS[key]; rows = rows_for(hist, key, ref)
    if len(rows) < MIN_ROWS:
        return {"available": False, "label": label, "n_rows": len(rows), "reason": f"إحصاءات {label} غير كافية في المصدر ({len(rows)} مباراة، المطلوب {MIN_ROWS})" + ("؛ قد لا يوفر المصدر إحصاءات كل شوط على حدة" if key.endswith(("_1h", "_2h")) else "")}
    m = _model(rows, ref, key)
    try: lh, la = expect(m, hid, aid)
    except ValueError: return {"available": False, "label": label, "n_rows": len(rows), "reason": f"أحد الفريقين لديه أقل من {MIN_TEAM} مباريات بإحصاءات {label}"}
    mu, ph = total_dist(lh, la, m["phi"]); n = len(rows)
    tot, hv, av = [r[3] + r[4] for r in rows], [r[3] for r in rows], [r[4] for r in rows]
    bt = lambda L: sum(1 for x in tot if x > L) / n
    bs = lambda L: (sum(1 for x in hv if x > L) + sum(1 for x in av if x > L)) / (2 * n)
    pw, pdr, pl = race(lh, la, m["phi"])
    out = lambda vals: tuple(round(sum(1 for v in vals if f(v)) / len(vals), 4) for f in (lambda v: v > 0, lambda v: v == 0, lambda v: v < 0))
    bq = out([r[3] - r[4] for r in rows])
    def hist_of(t):
        d = [(r[3] - r[4]) if r[1] == t else (r[4] - r[3]) for r in rows if t in (r[1], r[2])]
        return {"n": len(d), "more": sum(1 for v in d if v > 0), "tie": sum(1 for v in d if v == 0), "less": sum(1 for v in d if v < 0)}
    return {"race": {"home": pw, "draw": pdr, "away": pl, "base": {"home": bq[0], "draw": bq[1], "away": bq[2]}, "hist": {"home": hist_of(hid), "away": hist_of(aid)}},
            "available": True, "label": label, "exp_home": round(lh, 2), "exp_away": round(la, 2), "exp_total": round(mu, 2), "phi": round(m["phi"], 3),
            "n_rows": n, "n_home": m["counts"].get(hid, 0), "n_away": m["counts"].get(aid, 0),
            "lines": [{"line": L, "over": p_over(mu, ph, L), "base": bt(L)} for L in tl],
            "team_lines": {"home": [{"line": L, "over": p_over(lh, m["phi"], L), "base": bs(L)} for L in ml],
                           "away": [{"line": L, "over": p_over(la, m["phi"], L), "base": bs(L)} for L in ml]}}

def grade(gain): return "جيد" if gain > 0.003 else "مقبول" if gain > -0.001 else "ضعيف"

def evaluate_all(rows, with_race=False, test_days=240, refit_days=60, min_train=MIN_ROWS):
    """تقييم تاريخي بتدريب واحد لكل نافذة: (تقييم المجموع، تقييم من يفوز بالعدد). نصف تكلفة التقييمين المنفصلين."""
    rows = sorted(rows)
    if len(rows) < min_train + 60: return None, None
    last = rows[-1][0]; cur = last - timedelta(days=test_days); tp, rp, rb = [], [], []
    oc = lambda v: 0 if v > 0 else 1 if v == 0 else 2
    while cur <= last:
        end = cur + timedelta(days=refit_days)
        train = [r for r in rows if r[0] < cur]; test = [r for r in rows if cur <= r[0] < end]
        if len(train) >= min_train and test:
            tot = sorted(r[3] + r[4] for r in train); line = math.floor(tot[len(tot) // 2]) + 0.5
            fr = sum(1 for x in tot if x > line) / len(tot); m = fit(train, cur)
            frr = [sum(1 for r in train if oc(r[3] - r[4]) == k) / len(train) for k in range(3)] if with_race else None
            for d, h, a, vh, va, *_ in test:
                try: lh, la = expect(m, h, a)
                except ValueError: continue
                mu, ph = total_dist(lh, la, m["phi"]); tp.append((p_over(mu, ph, line), fr, int(vh + va > line)))
                if with_race: o = oc(vh - va); rp.append((*race(lh, la, m["phi"]), o)); rb.append((*frr, o))
        cur = end
    r = backtest._bin(tp); tot_ev = race_ev = None
    if r and r["n"] >= 100: gain = round(r["baseline_log_loss"] - r["log_loss"], 4); r["log_loss_gain"] = gain; r["grade"] = grade(gain); tot_ev = r
    if with_race:
        sc, sb = backtest._score(rp), backtest._score(rb)
        if sc and sc["n"] >= 100:
            gain = round(sb["log_loss"] - sc["log_loss"], 4)
            race_ev = {"n": sc["n"], "log_loss": sc["log_loss"], "baseline_log_loss": sb["log_loss"], "log_loss_gain": gain, "grade": grade(gain)}
    return tot_ev, race_ev

def evaluate(rows, **kw): return evaluate_all(rows, False, **kw)[0]
def evaluate_race(rows, **kw): return evaluate_all(rows, True, **kw)[1]
