"""تحليل مستقل لآخر 50 مباراة لكل فريق (الدوري + دوري الأبطال): تكرار القيم من الأكثر إلى الأقل وعدد المباريات لكل قيمة."""
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from . import pitch_client as pc

N = 50
NAMES = {}   # team_id -> name (يُملأ أثناء قراءة المباريات)
# (مفاتيح مطابقة تامة، أجزاء نصية احتياطية). الحقول غير موثقة بأسمائها، لذا يُبحث عنها بالاسم وتُعرض المتاحة فعليًا.
WANT = {"corners": (["corners", "corner_kicks"], ["corner"]), "yellow": (["yellow_cards", "yellowcards"], ["yellow"]),
        "fouls": (["fouls", "fouls_committed"], ["foul"]), "shots": (["total_shots"], ["total shots"]),
        "sot": (["shots_on_target"], ["shots on target"]), "xg": (["expected_goals"], [])}
LINES = {"goals": ([1.5, 2.5, 3.5], [0.5, 1.5, 2.5]), "corners": ([7.5, 8.5, 9.5, 10.5, 11.5], [3.5, 4.5, 5.5, 6.5]),
         "yellow": ([2.5, 3.5, 4.5, 5.5], [0.5, 1.5, 2.5]), "shots": ([22.5, 25.5, 28.5], [10.5, 12.5, 14.5]),
         "sot": ([7.5, 8.5, 9.5], [3.5, 4.5, 5.5])}

def _num(s):
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(s)); return float(m.group(1)) if m else None

def _norm(x): return re.sub(r"[^a-z0-9]", "", str(x).lower())

def _kind(p):
    n = _norm(p.get("period", ""))
    if n in ("all", "match", "full", "fulltime", "total", "ft"): return "all"
    if "first" in n or "1st" in n or n.startswith("1") or n.endswith("1") or n == "h1": return "1h"
    if "second" in n or "2nd" in n or n.startswith("2") or n.endswith("2") or n == "h2": return "2h"
    return None

def _items_of(p): return [it for g in p.get("groups", []) for it in g.get("items", [])]

def _periods(data):
    out = {}
    for p in data.get("periods") or []:
        k = _kind(p)
        if k and k not in out: out[k] = _items_of(p)
    return out

def _items(data):
    ps = _periods(data)
    if "all" in ps: return ps["all"]
    plain = [p for p in (data.get("periods") or []) if _kind(p) is None]   # لا نعتبر شوطًا واحدًا "مجمل المباراة" أبدًا
    return _items_of(plain[0]) if plain else []

def _pairs(items):
    out = {}
    for name, (exact, subs) in WANT.items():
        pick = next((it for t in exact for it in items if str(it.get("key", "")).lower() == t), None)
        if not pick:
            pick = next((it for t in subs for it in items if t in str(it.get("key", "")).lower() or t in str(it.get("title", "")).lower()), None)
        if pick:
            h, a = _num(pick.get("home")), _num(pick.get("away"))
            if h is not None and a is not None: out[name] = (h, a)
    return out

def parse_stats(data):
    out = _pairs(_items(data)); ps = _periods(data)
    for k in ("1h", "2h"):   # ركنيات كل شوط إن وفّرها المصدر
        if k in ps:
            c = _pairs(ps[k]).get("corners")
            if c: out[f"corners_{k}"] = c
    return out

def _match_stats(mid, hid, aid):
    out = {}
    try: out = parse_stats(pc.get(f"/v1/matches/{mid}/stats", ttl=24 * 3600) or {})
    except pc.PitchError: pass
    if "yellow" not in out:   # احتياط: عدّ البطاقات الصفراء من أحداث المباراة
        try:
            evs = pc.get(f"/v1/matches/{mid}/events", ttl=24 * 3600).get("events") or []
            if evs: out["yellow"] = tuple(float(sum(1 for e in evs if str(e.get("event_type", "")).lower().replace("_", "") == "yellowcard"
                                                    and e.get("team_id") == t)) for t in (hid, aid))
        except pc.PitchError: pass
    return out

def _freq(L): return [{"value": int(v), "matches": c} for v, c in sorted(Counter(L).items(), key=lambda kv: (-kv[1], kv[0]))]
def _cnt(L, lines): return [{"line": l, "over": sum(1 for x in L if x > l), "under": sum(1 for x in L if x < l)} for l in lines]

def _metric(name, vals):
    f, a = [v[0] for v in vals], [v[1] for v in vals]; t = [x + y for x, y in vals]; avg = lambda L: round(sum(L) / len(L), 2)
    out = {"n": len(vals), "for_avg": avg(f), "against_avg": avg(a), "total_avg": avg(t)}
    if all(float(x).is_integer() for x in t):
        out.update(total_freq=_freq(t), for_freq=_freq(f), max_total={"value": int(max(t)), "matches": t.count(max(t))},
                   min_total={"value": int(min(t)), "matches": t.count(min(t))})
        if name in LINES: out["lines"], out["team_lines"] = _cnt(t, LINES[name][0]), _cnt(f, LINES[name][1])
    return out

def _games(allh, team, ref, n=N):
    seen, g = set(), []
    for x in sorted((x for x in allh if team in (x[1], x[2]) and x[0] < ref), reverse=True):
        if x[5] not in seen: seen.add(x[5]); g.append(x)
    return g[:n]

def _team(games, team, stats):
    z = lambda: {"W": 0, "D": 0, "L": 0}
    vals, res, ven, comp, btts, clean, gfs, gas, rows = {}, z(), {"home": z(), "away": z()}, {"league": z(), "ucl": z()}, 0, 0, 0, 0, []
    for d, h, a, gh, ga, mid, tag, *rest in games:
        home = h == team; gf, ag = (gh, ga) if home else (ga, gh)
        r = "W" if gf > ag else "D" if gf == ag else "L"
        res[r] += 1; ven["home" if home else "away"][r] += 1; comp[tag][r] += 1; gfs += gf; gas += ag
        btts += gf > 0 and ag > 0; clean += ag == 0
        vals.setdefault("goals", []).append((float(gf), float(ag)))
        st = stats.get(mid, {})
        for k, (x, y) in st.items(): vals.setdefault(k, []).append((x, y) if home else (y, x))
        opp = a if home else h; cp = st.get("corners")
        rows.append({"date": d.isoformat(), "opp": NAMES.get(opp) or opp, "home": home, "score": f"{gf}-{ag}", "r": r,
                     "comp": rest[0] if rest else ("دوري الأبطال" if tag == "ucl" else "الدوري"),
                     "corners": [int(cp[0]), int(cp[1])] if cp and home else ([int(cp[1]), int(cp[0])] if cp else None)})
    tags = Counter(g[6] for g in games)
    return {"n": len(games), "competitions": {"league": tags.get("league", 0), "champions_league": tags.get("ucl", 0)},
            "results": res, "venue_results": ven, "comp_results": {"league": comp["league"], "champions_league": comp["ucl"]},
            "gf": gfs, "ga": gas, "points": 3 * res["W"] + res["D"], "btts": btts, "clean_sheets": clean, "rows": rows,
            "metrics": {k: _metric(k, v) for k, v in vals.items()}}

LAB = {"goals": "الأهداف", "corners": "الركنيات", "yellow": "البطاقات الصفراء", "shots": "التسديدات", "sot": "التسديدات على المرمى"}

def _picks(comb, H, A, m, n):
    """أعلى الخطوط تكرارًا عند الفريقين معًا في العينة الأخيرة (وصفي بحت؛ العينة صغيرة)."""
    out = []
    for k, c in comb.items():
        for l in c.get("lines", []):
            tot = l["home_n"] + l["away_n"]
            if not tot: continue
            for side, hits in (("أكثر من", l["home"] + l["away"]), ("أقل من", tot - l["home"] - l["away"])):
                rate = round(100 * hits / tot)
                if rate >= 75: out.append({"market": LAB.get(k, k), "text": f"المجموع {side} {l['line']}", "hits": hits, "of": tot, "rate": rate})
    return sorted(out, key=lambda x: (-x["rate"], -x["of"]))[:8]

def build(m, allh, ref, n=N):
    hid, aid = m["home_team"]["id"], m["away_team"]["id"]
    gh, ga = _games(allh, hid, ref, n), _games(allh, aid, ref, n)
    todo = {g[5]: (g[1], g[2]) for g in gh + ga}
    with ThreadPoolExecutor(8) as ex: got = list(ex.map(lambda kv: _match_stats(kv[0], *kv[1]), todo.items()))
    stats = dict(zip(todo, got)); H, A = _team(gh, hid, stats), _team(ga, aid, stats)
    comb = {}
    for k in H["metrics"]:
        hm, am = H["metrics"][k], A["metrics"].get(k)
        if not am or hm["n"] < min(10, n) or am["n"] < min(10, n): continue
        eh, ea = (hm["for_avg"] + am["against_avg"]) / 2, (am["for_avg"] + hm["against_avg"]) / 2
        c = {"expected_total": round(eh + ea, 2), "expected_home": round(eh, 2), "expected_away": round(ea, 2)}
        if "lines" in hm and "lines" in am:
            c["lines"] = [{"line": x["line"], "home": x["over"], "home_n": hm["n"], "away": y["over"], "away_n": am["n"],
                           "avg_pct": round(50 * (x["over"] / hm["n"] + y["over"] / am["n"]))} for x, y in zip(hm["lines"], am["lines"])]
        comb[k] = c
    sample = []
    if gh:
        try: sample = [f'{it.get("key")} | {it.get("title")}' for it in _items(pc.get(f"/v1/matches/{gh[0][5]}/stats", ttl=24 * 3600) or {})][:80]
        except pc.PitchError: pass
    return {"match": {"id": m["id"], "home": m["home_team"]["name"], "away": m["away_team"]["name"], "league": m["league"]["name"],
                      "home_logo": m["home_team"].get("image_url"), "away_logo": m["away_team"].get("image_url")},
            "home": H, "away": A, "combined": comb, "available_stat_keys": sample,
            "n": n, "picks": _picks(comb, H, A, m, n) if n <= 10 else None,
            "note": f"تحليل وصفي لآخر {n} مباراة لكل فريق من الدوري ودوري الأبطال (إن وُجدت)، وليس نموذجًا احتماليًا."}

def _keys(mid):
    try: return [f'{it.get("key")} | {it.get("title")}' for it in _items(pc.get(f"/v1/matches/{mid}/stats", ttl=24 * 3600) or {})][:80]
    except pc.PitchError: return []

def build_team(team_id, name, allh, ref):
    games = _games(allh, team_id, ref)
    todo = {g[5]: (g[1], g[2]) for g in games}
    with ThreadPoolExecutor(8) as ex: got = list(ex.map(lambda kv: _match_stats(kv[0], *kv[1]), todo.items()))
    data = _team(games, team_id, dict(zip(todo, got)))
    try: logo = (pc.get(f"/v1/teams/{team_id}", ttl=7 * 86400) or {}).get("image_url")
    except pc.PitchError: logo = None
    return {"team": {"id": team_id, "name": name, "logo": logo}, "data": data, "available_stat_keys": _keys(games[0][5]) if games else [],
            "period": [min(g[0] for g in games).isoformat(), max(g[0] for g in games).isoformat()] if games else None,
            "note": "تحليل وصفي لآخر مباريات الفريق (حتى 50) من المسابقات المحسوبة، وليس نموذجًا احتماليًا."}
