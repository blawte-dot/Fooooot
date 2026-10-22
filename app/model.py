"""Poisson + Dixon-Coles مع أوزان زمنية. Python نقي بلا مكتبات ثقيلة."""
import math

MAXG = 10

def _pois(k, l): return math.exp(-l) * l**k / math.factorial(k)

def _tau(x, y, lh, la, rho):
    if x == 0 and y == 0: return 1 - lh * la * rho
    if x == 0 and y == 1: return 1 + lh * rho
    if x == 1 and y == 0: return 1 + la * rho
    if x == 1 and y == 1: return 1 - rho
    return 1.0

def score_matrix(lh, la, rho=0.0):
    m = [[max(_pois(i, lh) * _pois(j, la) * _tau(i, j, lh, la, rho), 0.0)
          for j in range(MAXG + 1)] for i in range(MAXG + 1)]
    s = sum(map(sum, m))
    return [[v / s for v in row] for row in m]

def summarize(m):
    n = len(m); P = lambda f: sum(m[i][j] for i in range(n) for j in range(n) if f(i, j))
    top = sorted(((m[i][j], i, j) for i in range(n) for j in range(n)), reverse=True)[:5]
    return {"home": P(lambda i, j: i > j), "draw": P(lambda i, j: i == j), "away": P(lambda i, j: i < j),
            "over15": P(lambda i, j: i + j > 1.5), "over25": P(lambda i, j: i + j > 2.5),
            "under25": P(lambda i, j: i + j < 2.5), "btts": P(lambda i, j: i > 0 and j > 0),
            "top_scores": [{"score": f"{i}-{j}", "p": p} for p, i, j in top]}

def round_to_100(vals):
    """تقريب إلى أعداد صحيحة مجموعها 100 (أكبر باقٍ)."""
    fl = [int(v * 100) for v in vals]; rem = 100 - sum(fl)
    for k in sorted(range(len(vals)), key=lambda k: vals[k] * 100 - fl[k], reverse=True)[:rem]: fl[k] += 1
    return fl

def fit(matches, ref_date, xi=0.0019, iters=150, shrink=2.0, use_dc=True, xg=None, alpha=0.0):
    """matches: [(date, home, away, gh, ga, id?)]. xg: {match_id: (xg_home, xg_away)}. alpha: وزن xG مقابل الأهداف الفعلية في تقدير القوة
    (يُستخدم الهدف الفعلي في تصحيح Dixon-Coles للنتائج المنخفضة). تُستخدم فقط المباريات قبل ref_date."""
    rows = []
    for m in matches:
        d, h, a, gh, ga = m[:5]
        if d >= ref_date: continue
        geh, gea = float(gh), float(ga)
        if alpha > 0 and xg and len(m) > 5 and m[5] in xg:
            xh, xa = xg[m[5]]; geh, gea = alpha * xh + (1 - alpha) * gh, alpha * xa + (1 - alpha) * ga
        rows.append(((ref_date - d).days, h, a, gh, ga, geh, gea))
    if not rows: raise ValueError("no matches")
    w = [math.exp(-xi * r[0]) for r in rows]
    teams = sorted({r[1] for r in rows} | {r[2] for r in rows})
    att = {t: 1.0 for t in teams}; dfn = dict(att); H = 1.3
    for _ in range(iters):
        num, den = {t: shrink for t in teams}, {t: shrink for t in teams}
        for wi, (_, h, a, gh, ga, geh, gea) in zip(w, rows):
            num[h] += wi * geh; den[h] += wi * dfn[a] * H
            num[a] += wi * gea; den[a] += wi * dfn[h]
        att = {t: num[t] / den[t] for t in teams}
        num, den = {t: shrink for t in teams}, {t: shrink for t in teams}
        for wi, (_, h, a, gh, ga, geh, gea) in zip(w, rows):
            num[a] += wi * geh; den[a] += wi * att[h] * H
            num[h] += wi * gea; den[h] += wi * att[a]
        dfn = {t: num[t] / den[t] for t in teams}
        H = sum(wi * r[5] for wi, r in zip(w, rows)) / sum(wi * att[r[1]] * dfn[r[2]] for wi, r in zip(w, rows))
        m = sum(att.values()) / len(att); att = {t: v / m for t, v in att.items()}; dfn = {t: v * m for t, v in dfn.items()}
    def ll(rho):
        s = 0.0
        for wi, (_, h, a, gh, ga, geh, gea) in zip(w, rows):
            t = _tau(gh, ga, att[h] * dfn[a] * H, att[a] * dfn[h], rho)
            if t <= 0: return -1e18
            s += wi * math.log(t)
        return s
    rho = max((r / 100 for r in range(-20, 11)), key=ll) if use_dc else 0.0
    counts = {}
    for r in rows:
        counts[r[1]] = counts.get(r[1], 0) + 1; counts[r[2]] = counts.get(r[2], 0) + 1
    return {"att": att, "def": dfn, "home_adv": H, "rho": rho, "counts": counts, "n": len(rows)}

def lambdas(model, home, away, min_games=8):
    c = model["counts"]
    if c.get(home, 0) < min_games or c.get(away, 0) < min_games:
        raise ValueError("insufficient_data")
    return (model["att"][home] * model["def"][away] * model["home_adv"],
            model["att"][away] * model["def"][home])

def predict(model, home, away, min_games=8):
    lh, la = lambdas(model, home, away, min_games)
    s = summarize(score_matrix(lh, la, model["rho"]))
    s["xg_home"], s["xg_away"] = lh, la
    return s

def poisson_over(lam, line):
    """P(X > line) لتوزيع بواسون."""
    return max(0.0, 1 - sum(_pois(k, lam) for k in range(int(line) + 1)))

def markets(m):
    n = len(m); T = lambda f: sum(m[i][j] for i in range(n) for j in range(n) if f(i, j))
    h, d, a = T(lambda i, j: i > j), T(lambda i, j: i == j), T(lambda i, j: i < j)
    top = sorted(((m[i][j], i, j) for i in range(n) for j in range(n)), reverse=True)[:10]
    return {
        "ou": {str(l): {"over": T(lambda i, j, l=l: i + j > l), "under": T(lambda i, j, l=l: i + j < l)}
               for l in (0.5, 1.5, 2.5, 3.5, 4.5)},
        "team_goals": {"home": {str(l): T(lambda i, j, l=l: i > l) for l in (0.5, 1.5, 2.5)},
                       "away": {str(l): T(lambda i, j, l=l: j > l) for l in (0.5, 1.5, 2.5)}},
        "double_chance": {"1X": h + d, "X2": d + a, "12": h + a},
        "dnb": {"home": h / (h + a), "away": a / (h + a)},
        "ah": {"home_m1": {"win": T(lambda i, j: i - j >= 2), "push": T(lambda i, j: i - j == 1), "lose": T(lambda i, j: i - j <= 0)},
               "away_m1": {"win": T(lambda i, j: j - i >= 2), "push": T(lambda i, j: j - i == 1), "lose": T(lambda i, j: j - i <= 0)}},
        "clean_sheet": {"home": T(lambda i, j: j == 0), "away": T(lambda i, j: i == 0)},
        "btts": {"yes": T(lambda i, j: i > 0 and j > 0), "no": T(lambda i, j: i == 0 or j == 0)},
        "correct_score": [{"score": f"{i}-{j}", "p": p} for p, i, j in top]}

def reweight(m, target):
    """يعيد وزن مصفوفة النتائج لتطابق احتمالات 1X2 النهائية مع بقاء الأسواق الأخرى متناسقة."""
    n = len(m); idx = lambda i, j: 0 if i > j else (1 if i == j else 2)
    cur = [0.0, 0.0, 0.0]
    for i in range(n):
        for j in range(n): cur[idx(i, j)] += m[i][j]
    f = [t / c if c > 0 else 1.0 for t, c in zip(target, cur)]
    return [[m[i][j] * f[idx(i, j)] for j in range(n)] for i in range(n)]

def expected_goals(m):
    n = len(m)
    return (sum(i * sum(m[i]) for i in range(n)), sum(j * sum(m[i][j] for i in range(n)) for j in range(n)))
