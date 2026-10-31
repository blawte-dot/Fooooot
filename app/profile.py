"""طبقة «ملف الفريق التحليلي»: أسلوب الفريق (هجومي/دفاعي)، سجله ضد الكبار والمتوسطين والصغار وضد أنماط الفرق، الركنيات والبطاقات،
أكثر من يسجل عليهم وأصعب خصومه، واتجاه الأداء، ثم تقاطع ملفَّي فريقين في مباراة. كلها حسابات من النتائج والإحصاءات المحفوظة (بلا شبكة)،
والذكاء الاصطناعي يشرحها فقط ولا يضيف عليها أرقامًا. التصنيف (كبار/متوسطون/صغار) بتصنيف Elo الحالي لفرق الدوري المحلي لا وقت كل مباراة."""
import math
from . import elo, last50

HI, LO = 1.12, 0.88             # عتبات المؤشر النسبي لمتوسط الدوري (هجوم/دفاع)
WINDOW = 800                    # أيام العينة لمتوسطات الدوري وأساليب الخصوم
TLAB = {"big": "ضد الكبار (أعلى 30% تصنيفًا)", "mid": "ضد المتوسطين", "small": "ضد الصغار (أدنى 30%)", "europe": "ضد فرق أوروبية (دوري الأبطال)"}
TIER_AR = {"big": "من الكبار", "mid": "من المتوسطين", "small": "من الصغار"}

def style_label(a, d):
    """a: مؤشر التسجيل، d: مؤشر الاستقبال (الأقل أفضل)، نسبةً لمتوسط الدوري."""
    if a >= HI and d >= HI: return "هجومي مفتوح (يسجل كثيرًا ويستقبل كثيرًا)"
    if a >= HI and d <= LO: return "مسيطر (هجوم قوي ودفاع صلب)"
    if a >= HI: return "هجومي"
    if d <= LO and a <= LO: return "دفاعي مغلق (قليل التسجيل والاستقبال)"
    if d <= LO: return "دفاعي صلب"
    if d >= HI and a <= LO: return "ضعيف في الجانبين"
    if d >= HI: return "هش دفاعيًا"
    if a <= LO: return "ضعيف هجوميًا"
    return "متوازن"

def opp_group(a, d):
    return "attack" if a >= HI else ("defense" if d <= LO or a <= LO else "other")

def league_ctx(dom_hist, ref):
    """سياق الدوري المحلي: متوسط الأهداف، تصنيف Elo (كبار/متوسطون/صغار)، وأسلوب كل فريق. يُحسب مرة لكل دوري."""
    recent = [h for h in dom_hist if h[0] < ref and (ref - h[0]).days <= WINDOW]
    if len(recent) < 60: return None
    L = sum(h[3] + h[4] for h in recent) / (2 * len(recent)); gf, ga, n = {}, {}, {}
    for h in recent:
        for t, f, a in ((h[1], h[3], h[4]), (h[2], h[4], h[3])): gf[t] = gf.get(t, 0) + f; ga[t] = ga.get(t, 0) + a; n[t] = n.get(t, 0) + 1
    active = {h[1] for h in recent if (ref - h[0]).days <= 400} | {h[2] for h in recent if (ref - h[0]).days <= 400}
    active = [t for t in active if n.get(t, 0) >= 10]
    R = elo.fit(dom_hist, ref)["R"]; order = sorted(active, key=lambda t: -R.get(t, 1500.0)); k = max(1, math.ceil(0.3 * len(order))); tier = {}
    for i, t in enumerate(order): tier[t] = "big" if i < k else ("small" if i >= len(order) - k else "mid")
    sty = {t: (round(gf[t] / n[t] / L, 2), round(ga[t] / n[t] / L, 2)) for t in n if L}
    return {"L": L, "tier": tier, "style": sty, "rank": {t: i + 1 for i, t in enumerate(order)}, "teams": len(order)}

def _agg(rows):
    n = len(rows)
    if not n: return {"n": 0}
    W = sum(r["r"] == "W" for r in rows); D = sum(r["r"] == "D" for r in rows); L_ = n - W - D
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    cr = [r["corners"] for r in rows if r["corners"]]; yr = [r["yellow"] for r in rows if r["yellow"]]; sr = [r["shots"] for r in rows if r["shots"]]
    return {"n": n, "W": W, "D": D, "L": L_, "gf": avg([r["gf"] for r in rows]), "ga": avg([r["ga"] for r in rows]), "ppg": round((3 * W + D) / n, 2),
            "over25_pct": round(100 * sum(r["gf"] + r["ga"] > 2.5 for r in rows) / n), "btts_pct": round(100 * sum(r["gf"] > 0 and r["ga"] > 0 for r in rows) / n),
            "draw_pct": round(100 * D / n), "cn": len(cr), "cf": avg([c[0] for c in cr]), "ca": avg([c[1] for c in cr]),
            "yn": len(yr), "yf": avg([c[0] for c in yr]), "ya": avg([c[1] for c in yr]), "sn": len(sr), "shf": avg([c[0] for c in sr]), "sha": avg([c[1] for c in sr])}

def build_profile(team_id, name, games, stats, ctx, ref):
    """games: صفوف (التاريخ، المضيف، الضيف، أهداف المضيف، أهداف الضيف، المعرّف، الوسم، ...) الأحدث أولًا؛ stats: معرّف المباراة -> {مفتاح: (مضيف، ضيف)}."""
    rows = []
    for d, h, a, gh, ga, mid, tag, *rest in games:
        home = h == team_id; gf, ag = (gh, ga) if home else (ga, gh); opp = a if home else h; st = stats.get(mid) or {}
        o = lambda k: ((st[k][0], st[k][1]) if home else (st[k][1], st[k][0])) if k in st else None
        t = "europe" if tag == "ucl" else (ctx["tier"].get(opp) if ctx else None); sty = ctx["style"].get(opp) if ctx else None
        rows.append({"d": d, "opp": opp, "home": home, "gf": gf, "ga": ag, "r": "W" if gf > ag else "D" if gf == ag else "L", "tag": tag, "tier": t,
                     "grp": opp_group(*sty) if sty and tag != "ucl" else None, "corners": o("corners"), "yellow": o("yellow"), "shots": o("shots"), "c1h": o("corners_1h")})
    if not rows: return None
    out = {"team": {"id": team_id, "name": name}, "n": len(rows), "overall": _agg(rows), "venue": {"home": _agg([r for r in rows if r["home"]]), "away": _agg([r for r in rows if not r["home"]])}}
    dom = [r for r in rows if r["tag"] == "league" and (ref - r["d"]).days <= WINDOW]; sel = dom if len(dom) >= 10 else rows
    gfa, gaa = sum(r["gf"] for r in sel) / len(sel), sum(r["ga"] for r in sel) / len(sel)
    L = ctx["L"] if ctx else (sum(r["gf"] + r["ga"] for r in rows) / (2 * len(rows)))
    a_, d_ = round(gfa / L, 2) if L else 1.0, round(gaa / L, 2) if L else 1.0
    out["style"] = {"label": style_label(a_, d_), "attack": a_, "defense": d_, "gf_avg": round(gfa, 2), "ga_avg": round(gaa, 2), "league_avg": round(L, 2), "n": len(sel), "vs_league": bool(ctx)}
    out["own_tier"] = (ctx or {}).get("tier", {}).get(team_id); out["own_rank"] = (ctx or {}).get("rank", {}).get(team_id); out["league_teams"] = (ctx or {}).get("teams")
    out["tiers"] = [{"key": k, "label": TLAB[k], **_agg([r for r in rows if r["tier"] == k])} for k in ("big", "mid", "small", "europe")]
    out["vs_style"] = [{"key": k, "label": lab, **_agg([r for r in rows if r["grp"] == k])} for k, lab in (("attack", "ضد فرق هجومية"), ("defense", "ضد فرق دفاعية/مغلقة"))]
    by = {}
    for r in rows: by.setdefault(r["opp"], []).append(r)
    ag = [{"opp": o_, "opp_name": last50.NAMES.get(o_) or o_, **_agg(v)} for o_, v in by.items() if len(v) >= 2]
    out["scores_most_vs"] = sorted(ag, key=lambda x: (-x["gf"], -x["n"]))[:5]; out["struggles_vs"] = sorted(ag, key=lambda x: (x["ppg"], -x["ga"]))[:5]
    if len(rows) >= 20: out["trend"] = {"last10": _agg(rows[:10]), "before": _agg(rows[10:])}
    c1 = [r for r in rows if r["corners"] and r["c1h"]]
    cs = sum(r["corners"][0] for r in c1)
    out["corners_halves"] = {"n": len(c1), "first_half_share_for": round(100 * sum(r["c1h"][0] for r in c1) / cs) if c1 and cs else None}
    out["note"] = "حسابات وصفية من النتائج والإحصاءات المحفوظة (لا نموذج احتمالي). التصنيف بـElo الحالي لفرق الدوري المحلي، والعينات الصغيرة (أقل من 5 مباريات) ضعيفة الدلالة."
    return out

def _row(P, key):
    return next((x for x in P["tiers"] if x["key"] == key), None)

def matchup(HP, AP):
    """تقاطع ملفَّي المضيف والضيف: نصوص بأرقامها + حقائق مضغوطة للذكاء الاصطناعي. لا احتمالات جديدة."""
    if not HP or not AP: return None
    notes, facts = [], {}
    hs, as_ = HP["style"], AP["style"]
    facts["home_style"] = {"label": hs["label"], "attack": hs["attack"], "defense": hs["defense"]}; facts["away_style"] = {"label": as_["label"], "attack": as_["attack"], "defense": as_["defense"]}
    if hs["label"].startswith(("هجومي", "مسيطر")) and as_["defense"] >= HI: notes.append(f"هجوم المضيف ({hs['attack']}×) يواجه دفاعًا هشًّا ({as_['defense']}×): مناخ تسجيل مرتفع للمضيف.")
    if as_["label"].startswith(("هجومي", "مسيطر")) and hs["defense"] >= HI: notes.append(f"هجوم الضيف ({as_['attack']}×) يواجه دفاعًا هشًّا ({hs['defense']}×): مناخ تسجيل مرتفع للضيف.")
    if hs["defense"] <= LO and as_["defense"] <= LO: notes.append("دفاعان صلبان معًا: يميل اللقاء إلى القلة في الأهداف.")
    if hs["attack"] >= HI and as_["attack"] >= HI: notes.append("هجومان قويان معًا: يميل اللقاء إلى كثرة الأهداف.")
    for side, P, other, nm in (("home", HP, AP, "المضيف"), ("away", AP, HP, "الضيف")):
        t = other.get("own_tier")
        if t:
            r = _row(P, t)
            if r and r["n"] >= 3:
                notes.append(f"{nm} ضد فرق {TIER_AR[t]} (مثل خصمه): {r['n']} مباريات: فاز {r['W']} وتعادل {r['D']} وخسر {r['L']}، سجّل {r['gf']} واستقبل {r['ga']} في المباراة.")
                facts[f"{side}_vs_opp_tier"] = {"tier": t, "n": r["n"], "W": r["W"], "D": r["D"], "L": r["L"], "gf": r["gf"], "ga": r["ga"]}
        o = P["overall"]; facts[f"{side}_overall"] = {"n": o["n"], "W": o["W"], "D": o["D"], "L": o["L"], "gf": o["gf"], "ga": o["ga"], "draw_pct": o["draw_pct"]}
        if P["scores_most_vs"]: facts[f"{side}_scores_most_vs_n"] = P["scores_most_vs"][0]["n"]
    ce = {}
    for side, P, other in (("home", HP, AP), ("away", AP, HP)):
        a_, b_ = P["overall"], other["overall"]
        if a_.get("cn", 0) >= 8 and b_.get("cn", 0) >= 8 and a_.get("cf") is not None and b_.get("ca") is not None:
            t = other.get("own_tier"); r = _row(P, t) if t else None; basis = "عام"
            f = a_["cf"]
            if r and r.get("cn", 0) >= 4 and r.get("cf") is not None: f, basis = r["cf"], f"ضد فرق {TIER_AR[t]}"
            ce[side] = round((f + b_["ca"]) / 2, 2); ce[side + "_basis"] = basis
    if "home" in ce and "away" in ce:
        ce["total"] = round(ce["home"] + ce["away"], 2); notes.append(f"الركنيات المتوقعة من الملفين: المضيف {ce['home']} والضيف {ce['away']} (المجموع {ce['total']}).")
        facts["corners_expect"] = {"home": ce["home"], "away": ce["away"], "total": ce["total"]}
    for side, P, nm in (("home", HP, "المضيف"), ("away", AP, "الضيف")):
        o = P["overall"]
        if o.get("cn", 0) >= 8 and o.get("ca") is not None and o["ca"] >= 5.5: notes.append(f"{nm} يتلقى ركنيات كثيرة ({o['ca']} في المباراة).")
    return {"home": HP, "away": AP, "notes": notes, "corners_expect": ce or None, "facts": facts}
