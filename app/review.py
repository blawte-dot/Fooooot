"""قبل / أثناء / بعد المباراة، من المعطيات فقط:
- لقطة التوقع قبل الانطلاق (تُحفظ ولا تُعدَّل بعد البداية، فلا تسريب للنتيجة).
- نموذج حي: يعيد حساب الاحتمالات من النتيجة والدقيقة الحاليتين ومعدلات التسجيل المتوقعة قبل المباراة.
- مراجعة بعد النهاية: تقييم كل «نعم» وكل احتمال مقابل ما حدث فعلًا، ومقارنة المتوقع بالفعلي.
- سجل أداء تراكمي حقيقي (معايرة الاحتمالات) لا يتأثر بالتقديرات بأثر رجعي."""
import re, math
from datetime import datetime, timezone

RES = {"H": 0, "D": 1, "A": 2}
LIVE_NOT = {"not_started", "scheduled", "upcoming", "finished", "ft", "final", "completed", "ended", "postponed", "cancelled", "canceled", "abandoned", "suspended", "tbd", "ns", ""}
RED_W = (0.78, 1.25)    # تقدير شائع: الفريق الذي طُرد يهبط معدل تسجيله ×0.78 ويصعد معدل خصمه ×1.25 (افتراض معلن لا مُعاير)

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

FIN = {"finished", "ft", "final", "completed", "ended"}
PRE = {"not_started", "scheduled", "upcoming", "tbd", "ns", "postponed", "cancelled", "canceled", ""}

def phase_of(m):
    """pre / live / post / stale. المصدر لا يحدّث الحالة أثناء المباراة (تبقى not_started)، فالمباراة التي مضى على وقتها time_utc أقل من 150 دقيقة وليست منتهية تُعدّ جارية.
    ما تجاوز 150 دقيقة بلا نتيجة = stale (بيانات لم تُحدَّث بعد)."""
    st = str(m.get("status", "")).lower().replace(" ", "_"); played = m.get("score_home") is not None and m.get("score_away") is not None
    if st in FIN: return "post"
    if st in PRE:
        if played: return "post"
        k = _iso(m.get("time_utc"))
        if k and k.tzinfo:
            mins = (datetime.now(timezone.utc) - k).total_seconds() / 60
            if st in ("postponed", "cancelled", "canceled"): return "pre"
            if 0 <= mins <= 150: return "live"
            if mins > 150: return "stale"
        return "pre"
    return "live" if st not in LIVE_NOT else "pre"

# ---------- لقطة ما قبل المباراة ----------
def snapshot(p, b, date=None):
    pr = (p or {}).get("prediction")
    if not pr: return None
    m = p["match"]; hn, an = m["home"], m["away"]
    main = (f"فوز {hn}", "تعادل", f"فوز {an}")
    ev = [[e["group"], e["label"], e["p"], e.get("call"), e.get("grade")] for e in (b or {}).get("events", []) if e.get("call") == "نعم" or (e["group"] == "result" and e["label"] in main)]
    exp = {g["key"]: {"label": g["label"], **g["expected"]} for g in (b or {}).get("groups", []) if g.get("available") and g.get("expected")}
    return {"ts": now(), "hn": hn, "an": an, "league": m.get("league"), "date": date or m.get("date"), "p": [pr["home"], pr["draw"], pr["away"]],
            "xg": [pr["xg_home"], pr["xg_away"]], "conf": (p.get("confidence") or {}).get("level"), "ev": ev, "exp": exp}

# ---------- تقييم حدث واحد بعد النهاية ----------
def evaluate(group, label, hn, an, act):
    """True/False إن أمكن الحكم من النتيجة والإحصاءات الفعلية، وإلا None (لا نخمّن)."""
    if group == "result":
        h, a = act["goals"]
        return {f"فوز {hn}": h > a, "تعادل": h == a, f"فوز {an}": h < a, f"{hn} أو تعادل": h >= a, f"{an} أو تعادل": h <= a, "أحد الفريقين يفوز (لا تعادل)": h != a}.get(label)
    v = act.get(group)
    if not v: return None
    h, a = v
    if group == "goals":
        if label == "كلا الفريقين يسجل: نعم": return h > 0 and a > 0
        if label == "كلا الفريقين يسجل: لا": return h == 0 or a == 0
        if label == f"{hn} شباكه نظيفة": return a == 0
        if label == f"{an} شباكه نظيفة": return h == 0
    m = re.match(r"^المجموع (أكثر|أقل) من (\d+(?:\.\d+)?)$", label)
    if m: x = float(m.group(2)); return (h + a > x) if m.group(1) == "أكثر" else (h + a < x)
    for nm, val in ((hn, h), (an, a)):
        m = re.match(rf"^{re.escape(nm)}(?: يسجل)? (أكثر|أقل) من (\d+(?:\.\d+)?)$", label)
        if m: x = float(m.group(2)); return (val > x) if m.group(1) == "أكثر" else (val < x)
    if label == f"{hn} الأكثر عددًا": return h > a
    if label == "تعادل في العدد": return h == a
    if label == f"{an} الأكثر عددًا": return h < a
    return None

def review(snap, m, stats=None):
    h, a = int(m["score_home"]), int(m["score_away"]); out = "H" if h > a else "A" if h < a else "D"; ph = snap["p"]; i = RES[out]
    brier = sum((ph[k] / 100 - (1 if k == i else 0)) ** 2 for k in range(3))
    act = {"goals": (h, a), **{k: v for k, v in (stats or {}).items() if isinstance(v, (tuple, list)) and len(v) == 2}}
    picks = [{"group": g, "label": lab, "p": p, "call": call, "grade": grade, "ok": evaluate(g, lab, snap["hn"], snap["an"], act)} for g, lab, p, call, grade in snap["ev"]]
    comp = []
    for k, e in snap["exp"].items():
        v = act.get(k)
        if v: comp.append({"key": k, "label": e["label"], "exp": {"home": e["home"], "away": e["away"], "total": e["total"]}, "act": {"home": v[0], "away": v[1], "total": v[0] + v[1]}})
    return {"state": "post", "score": [h, a], "result": out, "p_actual": ph[i], "top_hit": ph.index(max(ph)) == i, "brier": round(brier, 3),
            "picks": picks, "compare": comp, "retro": bool(snap.get("retro")), "stats_ok": len(act) > 1}

# ---------- النموذج الحي ----------
def _pois(k, l): return math.exp(-l) * l ** k / math.factorial(k)

def inplay(sh, sa, minute, lh, la, rh=0, ra=0):
    """احتمالات النهاية من (النتيجة، الدقيقة) ومعدلات التسجيل المتوقعة للمباراة كاملة. افتراضات: توزيع الأهداف منتظم زمنيًا، وبواسون مستقل للمتبقي."""
    minute = max(0, min(float(minute), 120)); left = max(0.0, 90 - minute) / 90
    if minute >= 90: left = 0.02 if minute < 96 else 0.0
    mh = lh * left * (RED_W[0] ** rh) * (RED_W[1] ** ra); ma = la * left * (RED_W[0] ** ra) * (RED_W[1] ** rh)
    K = 12; H = D = A = 0.0; tot = {}; btts = 0.0
    for i in range(K):
        pi = _pois(i, mh)
        for j in range(K):
            pr = pi * _pois(j, ma); gh, ga = sh + i, sa + j
            if gh > ga: H += pr
            elif gh == ga: D += pr
            else: A += pr
            tot[gh + ga] = tot.get(gh + ga, 0.0) + pr
            if gh > 0 and ga > 0: btts += pr
    s = H + D + A or 1.0; H, D, A = H / s, D / s, A / s
    cur = sh + sa; lines = []
    for x in (0.5, 1.5, 2.5, 3.5, 4.5):
        if x > cur - 0.0001 and x >= cur: lines.append({"line": x, "over": round(100 * sum(v for t, v in tot.items() if t > x) / s, 1)})
    lam = mh + ma; nxt = (1 - math.exp(-lam)) if lam > 0 else 0.0
    ng = {"home": round(100 * nxt * mh / lam, 1), "away": round(100 * nxt * ma / lam, 1), "none": round(100 * (1 - nxt), 1)} if lam > 0 else {"home": 0.0, "away": 0.0, "none": 100.0}
    return {"p": [round(100 * H, 1), round(100 * D, 1), round(100 * A, 1)], "lines": lines, "btts": round(100 * btts / s, 1), "next_goal": ng, "left_xg": [round(mh, 2), round(ma, 2)]}

def _iso(x):
    try: return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except Exception: return None

def minute_of(m, evs=None):
    """الدقيقة من أي حقل يوفره المصدر؛ وإلا من وقت الانطلاق؛ وإلا من آخر حدث (حد أدنى). تُرجع (دقيقة، المصدر)."""
    for k in ("minute", "elapsed", "current_minute", "match_minute", "clock", "time"):
        v = m.get(k)
        if v is not None and re.match(r"^\s*\d+", str(v)): return int(re.match(r"\s*(\d+)", str(v)).group(1)), k
    for k in ("time_utc", "kickoff", "kickoff_time", "start_time", "starts_at", "datetime", "scheduled_at", "date_time", "utc_date"):
        d = _iso(m.get(k))
        if d and d.tzinfo:
            mins = (datetime.now(timezone.utc) - d).total_seconds() / 60
            if 0 <= mins < 150: return int(mins if mins <= 47 else (mins - 15 if mins >= 62 else 46)), f"{k} (تقدير)"
    mm = [int(re.match(r"\s*(\d+)", str(e.get("minute"))).group(1)) for e in (evs or []) if e.get("minute") is not None and re.match(r"^\s*\d+", str(e.get("minute")))]
    return (max(mm), "آخر حدث (حد أدنى)") if mm else (None, None)

def reds_of(m, evs):
    hid, aid = (m.get("home_team") or {}).get("id"), (m.get("away_team") or {}).get("id"); rh = ra = 0
    for e in evs or []:
        t = str(e.get("event_type", "")).lower().replace("_", "").replace(" ", "")
        if "red" in t:
            if e.get("team_id") == hid: rh += 1
            elif e.get("team_id") == aid: ra += 1
    return rh, ra

def goals_from_events(m, evs):
    """نتيجة تقريبية من أحداث الأهداف إن لم يوفّر المصدر نتيجة جارية. تُرجع (مضيف، ضيف) أو None إن لم تتوفر أحداث."""
    if not evs: return None
    hid, aid = (m.get("home_team") or {}).get("id"), (m.get("away_team") or {}).get("id"); sh = sa = 0
    for e in evs:
        t = str(e.get("event_type", "")).lower().replace("_", "").replace(" ", "")
        if "goal" not in t or any(x in t for x in ("disallow", "cancel", "missed", "var", "nogoal")): continue
        own = "own" in t; tid = e.get("team_id")
        if tid == hid: sa, sh = (sa + 1, sh) if own else (sa, sh + 1)
        elif tid == aid: sh, sa = (sh + 1, sa) if own else (sh, sa + 1)
    return sh, sa

def live_state(m, snap, evs=None):
    minute, src = minute_of(m, evs); rh, ra = reds_of(m, evs)
    if m.get("score_home") is not None and m.get("score_away") is not None: sc, ssrc = (int(m["score_home"]), int(m["score_away"])), "المصدر"
    else:
        sc = goals_from_events(m, evs); ssrc = "أحداث الأهداف (قد تكون ناقصة أو متأخرة)"
    out = {"state": "live", "score": list(sc) if sc else None, "score_src": ssrc if sc else None, "minute": minute, "minute_src": src, "reds": [rh, ra], "ts": now(), "pre": snap["p"] if snap else None}
    if snap and sc and minute is not None: out["inplay"] = inplay(sc[0], sc[1], minute, snap["xg"][0], snap["xg"][1], rh, ra)
    return out

# ---------- السجل التراكمي ----------
def new_tr(): return {"since": now(), "done": [], "result": {"n": 0, "hit": 0, "brier": 0.0, "ll": 0.0}, "picks": {}, "grades": {}}

def update_tr(tr, mid, rev, snap):
    """يُضاف مرة واحدة لكل مباراة، ولا تُحتسب التقديرات بأثر رجعي (retro)."""
    if snap.get("retro") or mid in tr["done"]: return False
    tr["done"] = (tr["done"] + [mid])[-4000:]; r = tr["result"]; r["n"] += 1; r["hit"] += 1 if rev["top_hit"] else 0; r["brier"] += rev["brier"]; r["ll"] += -math.log(max(rev["p_actual"] / 100, 1e-6))
    for x in rev["picks"]:
        if x["call"] != "نعم" or x["ok"] is None: continue
        b = str(min(90, max(50, int(x["p"] // 10 * 10)))); c = tr["picks"].setdefault(b, {"n": 0, "hit": 0, "sp": 0.0}); c["n"] += 1; c["hit"] += 1 if x["ok"] else 0; c["sp"] += x["p"]
        g = tr["grades"].setdefault(x["grade"] or "؟", {"n": 0, "hit": 0}); g["n"] += 1; g["hit"] += 1 if x["ok"] else 0
    return True

def tr_view(tr):
    r = tr["result"]; n = r["n"]
    return {"since": tr["since"], "matches": n, "top_hit_pct": round(100 * r["hit"] / n, 1) if n else None, "brier": round(r["brier"] / n, 3) if n else None, "logloss": round(r["ll"] / n, 3) if n else None,
            "picks": [{"bucket": f"{b}-{int(b) + 10}%" if b != "90" else "90%+", "n": c["n"], "claimed": round(c["sp"] / c["n"], 1), "actual": round(100 * c["hit"] / c["n"], 1)} for b, c in sorted(tr["picks"].items()) if c["n"]],
            "grades": [{"grade": g, "n": c["n"], "actual": round(100 * c["hit"] / c["n"], 1)} for g, c in tr["grades"].items() if c["n"]],
            "note": "حصيلة حقيقية لتوقعات حُفظت قبل انطلاق المباريات. العينة صغيرة في البداية، فلا تُبنَ عليها قبل أن تتجاوز مئات المباريات."}
