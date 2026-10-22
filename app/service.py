import re, time, threading, unicodedata, traceback
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from . import pitch_client as pc, model, cards, backtest, elo, stats, last50, xg as xgmod, counts, cache, espn

FM = {"47": "Premier League", "87": "LaLiga", "54": "Bundesliga", "55": "Serie A", "53": "Ligue 1"}  # معرّفات صور الدوريات في المصدر
_B5N = {"premier league": "Premier League", "english premier league": "Premier League", "laliga": "LaLiga", "la liga": "LaLiga",
        "primera division": "LaLiga", "primera división": "LaLiga", "bundesliga": "Bundesliga", "serie a": "Serie A", "ligue 1": "Ligue 1"}
_B5C = {"Premier League": {"ENG", "GBR", "GB", "UK", "EN"}, "LaLiga": {"ESP", "ES"}, "Bundesliga": {"GER", "DEU", "DE"},
        "Serie A": {"ITA", "IT"}, "Ligue 1": {"FRA", "FR"}}

def _fm(l):
    m = re.search(r"/leagues/(\d+)\.", l.get("image_url") or ""); return m.group(1) if m else None

def big5_label(l):
    i = _fm(l)
    if i in FM: return FM[i]
    lab = _B5N.get((l.get("name") or "").strip().lower())
    return lab if lab and (l.get("country_code") or "").upper() in _B5C[lab] else None

def is_ucl(l):
    if _fm(l) == "42": return True
    n = (l.get("name") or "").lower()
    return "champions league" in n and not any(x in n for x in ("afc", "caf", "concacaf", "conmebol", "women", "asia", "africa", "u19", "youth", "oceania"))

_FAMOUS = ["real madrid", "barcelona", "manchester city", "manchester united", "liverpool", "arsenal", "chelsea", "tottenham",
           "bayern", "paris saint", "paris sg", "psg", "juventus", "inter", "internazionale", "milan", "napoli", "atletico madrid",
           "dortmund", "leverkusen", "benfica", "porto", "ajax", "sporting cp", "galatasaray", "fenerbahce", "al hilal", "al nassr",
           "al ahli", "al ittihad", "al ahly", "zamalek", "wydad", "raja casablanca"]
_FAM_RE = re.compile(r"\b(" + "|".join(re.escape(x) for x in _FAMOUS) + r")\b")

def is_famous(name):
    n = "".join(c for c in unicodedata.normalize("NFKD", name or "") if not unicodedata.combining(c)).lower().replace("-", " ")
    return bool(_FAM_RE.search(n))

def famous_matches(day):
    """أشهر مباريات اليوم: نادٍ من قائمة الأندية المشهورة، أو دوري أبطال أوروبا. الترتيب بنقاط بسيطة معلنة (لا توجد بيانات شعبية في المصدر)."""
    ms = pc.get(f"/v1/date/{day}", {"status": "all"}, ttl=300).get("matches", [])
    byid = {l["id"]: l for l in pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]}; out = []
    for m in ms:
        l = byid.get(m["league"]["id"], {}); fam = [t["name"] for t in (m["home_team"], m["away_team"]) if is_famous(t["name"])]
        ucl, b5 = is_ucl(l) if l else False, big5_label(l) if l else None
        if not fam and not ucl: continue
        out.append({**m, "score": 3 * len(fam) + 3 * ucl + (1 if b5 else 0), "badges": {"famous": fam, "ucl": ucl, "big5": b5}})
    out.sort(key=lambda x: (-x["score"], x.get("time_utc") or ""))
    return {"date": day, "matches": out}

ID_RE = re.compile(r"^[a-z]_[0-9A-Za-z]{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TEAM_RE = re.compile(r"^t_[0-9A-Za-z]{6}$")
DEFAULT = {"xi": 0.0019, "shrink": 2.0, "w": 1.0, "T": 1.0, "alpha": 0.0}
BLOCK_TUNE = False
_params, _running = {}, set()

def _pc(o):
    if isinstance(o, float): return round(o * 100, 1)
    if isinstance(o, dict): return {k: _pc(v) for k, v in o.items()}
    if isinstance(o, list): return [_pc(v) for v in o]
    return o

def history(league_id, seasons, before):
    out = []
    for s in seasons:
        d = pc.get(f"/v1/leagues/{league_id}/matches", {"season": s}, ttl=6 * 3600)
        for m in d.get("matches", []):
            if m.get("score_home") is None or m.get("score_away") is None: continue
            md = date.fromisoformat(m["date"])
            if md < before:
                for sd in ("home_team", "away_team"):
                    if m[sd].get("name"): last50.NAMES[m[sd]["id"]] = m[sd]["name"]
                out.append((md, m["home_team"]["id"], m["away_team"]["id"], m["score_home"], m["score_away"], m["id"]))
    return out

def get_params(lg, hist, use_xg=False):
    key = (lg, use_xg); hit = _params.get(key)
    if hit and hit["until"] > time.time(): return hit["p"], "tuned" if hit["ok"] else "default"
    def work():
        try:
            t = backtest.tune(hist, xg=xgmod.STORE if use_xg else None)
            _params[key] = {"p": t or dict(DEFAULT), "until": time.time() + 24 * 3600, "ok": bool(t)}
        finally: _running.discard(key)
    if key not in _running:
        _running.add(key)
        if BLOCK_TUNE: work()
        else: threading.Thread(target=work, daemon=True).start()
    hit = _params.get(key)
    if hit: return hit["p"], "tuned" if hit["ok"] else "default"
    old = _params.get((lg, False))   # أثناء ضبط xG نستخدم آخر معاملات مضبوطة بدونه
    return (old["p"], "tuned") if old and old["ok"] else (dict(DEFAULT), "tuning")

MIN_GAMES = 8

def confidence(sc, horizon, status, pdc, pe):
    mn, pts, why = min(sc["home_n"], sc["away_n"]), 0, []
    if mn >= 40: pts += 2
    elif mn >= 20: pts += 1
    else: why.append(f"بيانات قليلة لأحد الفريقين ({mn} مباراة)")
    if horizon <= 14: pts += 1
    else: why.append(f"المباراة بعد {horizon} يومًا")
    if status == "tuned": pts += 1
    else: why.append("معاملات النموذج غير مضبوطة بعد")
    if pe:
        if max(abs(x - y) for x, y in zip(pdc, pe)) < 0.10: pts += 1
        else: why.append("Dixon-Coles وElo مختلفان بوضوح")
    return {"level": "عالية" if pts >= 5 else "متوسطة" if pts >= 3 else "منخفضة", "reasons": why,
            "note": "مؤشر تقديري بقاعدة بسيطة، وليس احتمال صحة التوقع"}

def _all_seasons(leagues, lid):
    return sorted(next((l["seasons"] for l in leagues if l["id"] == lid), []), reverse=True)

def _predict_match(match_id):
    m = pc.get(f"/v1/matches/{match_id}", ttl=300)
    ref = date.fromisoformat(m["date"]); lg = m["league"]["id"]
    hid, aid = m["home_team"]["id"], m["away_team"]["id"]
    leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]
    me = next((l for l in leagues if l["id"] == lg), None)
    hist = history(lg, _all_seasons(leagues, lg), ref)          # كل المواسم المتاحة للدوري
    cnt = lambda H, t: sum(1 for h in H if t in (h[1], h[2]))
    extra = []
    if me and (cnt(hist, hid) < MIN_GAMES or cnt(hist, aid) < MIN_GAMES):
        seen = {h[5] for h in hist}      # تكملة: مسابقات أخرى في نفس الدولة (كل مواسمها)
        for l in leagues:
            if l["id"] != lg and l.get("country_code") == me.get("country_code"):
                try: extra += [h for h in history(l["id"], _all_seasons(leagues, l["id"]), ref) if h[5] not in seen]
                except pc.PitchError: pass
    fit_hist = hist + extra
    last = max((h[0] for h in fit_hist), default=None)
    res = {"match": {"id": m["id"], "league": m["league"]["name"], "home": m["home_team"]["name"],
                     "away": m["away_team"]["name"], "time_utc": m.get("time_utc"), "referee": m.get("referee"),
                     "home_logo": m["home_team"].get("image_url"), "away_logo": m["away_team"].get("image_url")},
           "data_through": last.isoformat() if last else None,
           "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "n_matches": len(fit_hist),
           "not_used": ["Elo", "xG التاريخي", "الإصابات"], "horizon_days": (ref - date.today()).days,
           "scope": {"league_matches": len(hist), "extra_matches": len(extra), "home_n": cnt(fit_hist, hid), "away_n": cnt(fit_hist, aid)}}
    if len(fit_hist) < 100:
        return {**res, "prediction": None, "reason": "بيانات تاريخية غير كافية لهذا الدوري"}
    xst = xgmod.ensure(lg, fit_hist, ref); cov = xgmod.coverage(fit_hist, ref); usable = cov >= 0.6
    params, status = get_params(lg, hist, usable)
    alpha = params.get("alpha", 0.0) if usable else 0.0
    eff = min(ref, date.today() + timedelta(days=1))          # النموذج نفسه لكل المباريات القادمة في الدوري: يُحسب مرة ويُعاد استخدامه
    fk = (lg, eff, params["xi"], params["shrink"], alpha, len(fit_hist), max(h[0] for h in fit_hist), (len(xgmod.STORE) // 50) if alpha > 0 else 0)
    mdl = _FM.get_or(fk, 1800, lambda: model.fit(fit_hist, eff, xi=params["xi"], shrink=params["shrink"], xg=xgmod.STORE if alpha > 0 else None, alpha=alpha))
    try: lh, la = model.lambdas(mdl, hid, aid, MIN_GAMES)
    except ValueError:
        sc = res["scope"]
        return {**res, "prediction": None, "reason": f"بيانات الفريقين غير كافية حتى بعد جمع كل مواسم المصدر (منذ 2021): {m['home_team']['name']} {sc['home_n']} مباراة، {m['away_team']['name']} {sc['away_n']} مباراة (الحد الأدنى {MIN_GAMES})"}
    mat0 = model.score_matrix(lh, la, mdl["rho"]); s0 = model.summarize(mat0); pdc = (s0["home"], s0["draw"], s0["away"])
    pe = elo.probs(elo.fit(fit_hist, ref), hid, aid); w, T = params.get("w", 1.0), params.get("T", 1.0)
    final = elo.blend(pdc, pe, w, T) if pe and (w < 1.0 or T != 1.0) else pdc
    mat = model.reweight(mat0, final) if final != pdc else mat0
    xh, xa = model.expected_goals(mat)
    h, d, a = model.round_to_100(list(final))
    r1 = lambda t: [round(x * 100) for x in t] if t else None
    res["engine"] = {"dixon_coles": r1(pdc), "elo": r1(pe), "final": [h, d, a], "elo_weight": round(1 - w, 2) if pe else 0, "temperature": T}
    res["confidence"] = confidence(res["scope"], res["horizon_days"], status, pdc, pe)
    res["features"] = {"home": {"overall": stats.form(fit_hist, hid, ref), "venue": stats.form(fit_hist, hid, ref, venue=True)},
                       "away": {"overall": stats.form(fit_hist, aid, ref), "venue": stats.form(fit_hist, aid, ref, venue=False)},
                       "h2h": stats.h2h(fit_hist, hid, aid, ref)}
    res["prediction"] = {"home": h, "draw": d, "away": a, "xg_home": round(xh, 2), "xg_away": round(xa, 2),
                         "markets": _pc(model.markets(mat))}
    try: c = cards.predict(fit_hist, hid, aid, ref)
    except pc.PitchError: c = None
    res["cards"] = {**c, "lines": _pc(c["lines"])} if c else None
    res["params"] = {**params, "status": status}
    res["xg"] = {"alpha": alpha, "coverage": round(cov * 100), "loading": xst["running"], "done": xst["done"], "total": xst["total"]}
    res["warning"] = "تقدير إحصائي وليس نصيحة رهان ولا ضمانًا. الرهان فيه خسارة واردة."
    return res

_bt = {}
def backtest_league(league_id):
    hit = _bt.get(league_id)
    if hit and hit[0] > time.time(): return hit[1]
    leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]
    lg = next((l for l in leagues if l["id"] == league_id), None)
    if not lg: raise pc.PitchError("RESOURCE_NOT_FOUND", "الدوري غير موجود", 404)
    hist = history(league_id, sorted(lg["seasons"], reverse=True)[:3], date.today())
    if len(hist) < 400:
        res = {"league": lg["name"], "result": None, "reason": "بيانات تاريخية غير كافية لاختبار موثوق"}
    else:
        res = {"league": lg["name"], "total_history": len(hist), "result": backtest.run(hist),
               "market_comparison": "غير متاح: المصدر لا يوفر أسعار سوق تاريخية",
               "note": "Brier/LogLoss الأقل أفضل. النموذج مفيد فقط إذا تفوق على baseline."}
    _bt[league_id] = (time.time() + 6 * 3600, res)
    return res


def last50_match(match_id):
    m = pc.get(f"/v1/matches/{match_id}", ttl=300)
    ref = date.fromisoformat(m["date"]); lg = m["league"]["id"]
    leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]; byid = {l["id"]: l for l in leagues}
    ids = ([byid[lg]] if lg in byid else []) + [l for l in leagues if is_ucl(l) and l["id"] != lg]
    allh = []
    for l in ids:
        try: allh += [(*h, "ucl" if is_ucl(l) else "league", l["name"]) for h in history(l["id"], _all_seasons(leagues, l["id"]), ref)]
        except pc.PitchError: pass
    res = last50.build(m, allh, ref)
    res["competitions_used"] = [l["name"] for l in ids]
    if not any(is_ucl(l) for l in leagues): res["note"] += " لم أجد دوري أبطال أوروبا في كتالوج المصدر الحالي، فلم يُحسب."
    if ids and is_ucl(ids[0]): res["note"] += " المباراة في دوري الأبطال، فالمحسوب هنا مباريات دوري الأبطال فقط (دوريات الفرق المحلية غير محددة)."
    try:
        p = predict_match(match_id); pr = p.get("prediction")
        res["outlook"] = {"home": pr["home"], "draw": pr["draw"], "away": pr["away"], "xg_home": pr["xg_home"], "xg_away": pr["xg_away"],
                          "confidence": p["confidence"]["level"]} if pr else {"reason": p.get("reason")}
    except Exception: res["outlook"] = None
    return res

def logos(ids):
    ids = [i for i in ids if TEAM_RE.match(i)][:24]
    def one(i):
        try: return i, (pc.get(f"/v1/teams/{i}", ttl=7 * 86400) or {}).get("image_url")
        except pc.PitchError: return i, None
    with ThreadPoolExecutor(6) as ex: return dict(ex.map(one, ids))

# ---- فهرس الفرق (لا توجد نقطة بحث عن الفرق في المصدر، فيُبنى من مباريات كل دوري) ----
BLOCK_INDEX = False
_idx = {"state": "idle", "teams": {}, "leagues": [], "error": None}

def _build_index():
    try:
        leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]
        def one(l):
            ss = sorted(l.get("seasons") or [], reverse=True)
            if not ss: return None
            try: return l, pc.get(f"/v1/leagues/{l['id']}/matches", {"season": ss[0], "status": "all"}, ttl=24 * 3600)
            except pc.PitchError: return None
        with ThreadPoolExecutor(6) as ex: res = list(ex.map(one, leagues))
        teams = {}
        for r in res:
            if not r: continue
            l, d = r
            for m in d.get("matches", []):
                for side in ("home_team", "away_team"):
                    t = m[side]; e = teams.setdefault(t["id"], {"name": t["name"], "comps": {}})
                    e["comps"][l["id"]] = e["comps"].get(l["id"], 0) + 1
        _idx.update(teams=teams, leagues=leagues, state="ready", error=None)
    except pc.PitchError as e:
        _idx.update(state="failed", error=str(e))

def _start_index():
    if _idx["state"] in ("idle", "failed"):
        _idx["state"] = "building"
        if BLOCK_INDEX: _build_index()
        else: threading.Thread(target=_build_index, daemon=True).start()

def search_teams(q, big5=False):
    q = q.strip().lower()[:40]
    if _idx["state"] != "ready":
        _start_index()
        if _idx["state"] != "ready": return {"ready": False, "state": _idx["state"], "error": _idx["error"]}
    byid = {l["id"]: l for l in _idx["leagues"]}
    lab = lambda e: next((big5_label(byid[l]) for l in e["comps"] if l in byid and big5_label(byid[l])), None)
    hits = [(tid, e) for tid, e in _idx["teams"].items() if (q in e["name"].lower() if q else big5) and (not big5 or lab(e))]
    if big5: hits.sort(key=lambda x: (lab(x[1]), x[1]["name"]))
    else: hits.sort(key=lambda x: (-sum(x[1]["comps"].values()), x[1]["name"]))
    def ids(e):
        o = sorted(e["comps"], key=e["comps"].get, reverse=True)
        return o[:5] + [l for l in o[5:] if l in byid and is_ucl(byid[l])]
    return {"ready": True, "teams": [{"id": tid, "name": e["name"], "competitions": [byid[l]["name"] for l in ids(e)[:3] if l in byid],
                                      "comp_ids": ids(e), "big5": lab(e)} for tid, e in hits[:(130 if big5 else 15)]]}

def team50(team_id, scope="main", comps=None, name=None):
    """comps: معرّفات مسابقات محفوظة مع النادي المفضل (مرتبة بالأهمية) فلا حاجة لانتظار فهرس الفرق."""
    if comps:
        leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]; byid = {l["id"]: l for l in leagues}
        order = [c for c in comps if c in byid]
        if not order: raise pc.PitchError("INVALID_PARAMETER", "مسابقات غير صالحة", 400)
        nm = (name or team_id)[:60]
    else:
        if _idx["state"] != "ready":
            _start_index()
            if _idx["state"] != "ready": raise pc.PitchError("INDEX_NOT_READY", "فهرس الفرق قيد البناء، أعد المحاولة بعد قليل", 503)
        e = _idx["teams"].get(team_id)
        if not e: raise pc.PitchError("RESOURCE_NOT_FOUND", "الفريق غير موجود في الفهرس", 404)
        leagues = _idx["leagues"]; byid = {l["id"]: l for l in leagues}
        order = sorted(e["comps"], key=e["comps"].get, reverse=True); nm = e["name"]
    dom = next((l for l in order if not is_ucl(byid[l])), None)
    chosen = order if scope == "all" else ([dom] if dom else []) + [l for l in order if is_ucl(byid[l])]
    ref = date.today() + timedelta(days=1); allh = []
    for lid in chosen:
        try: allh += [(*h, "ucl" if is_ucl(byid[lid]) else "league", byid[lid]["name"]) for h in history(lid, _all_seasons(leagues, lid), ref)]
        except pc.PitchError: pass
    res = last50.build_team(team_id, nm, allh, ref)
    res["competitions_used"] = [byid[l]["name"] for l in chosen]
    return res


# ---- لوحة الأسواق: كل سوق يُختبر تاريخيًا قبل أن يُقترح منه شيء ----
_grades, _GL = {}, threading.Lock()

def _fit_hist(m, leagues, ref):
    lg, hid, aid = m["league"]["id"], m["home_team"]["id"], m["away_team"]["id"]; me = next((l for l in leagues if l["id"] == lg), None)
    hist = history(lg, _all_seasons(leagues, lg), ref); cnt = lambda H, t: sum(1 for h in H if t in (h[1], h[2])); extra = []
    if me and (cnt(hist, hid) < MIN_GAMES or cnt(hist, aid) < MIN_GAMES):
        seen = {h[5] for h in hist}
        for l in leagues:
            if l["id"] != lg and l.get("country_code") == me.get("country_code"):
                try: extra += [h for h in history(l["id"], _all_seasons(leagues, l["id"]), ref) if h[5] not in seen]
                except pc.PitchError: pass
    return hist, hist + extra

def _espn_slug(l):
    if is_ucl(l): return "uefa.champions"
    b = big5_label(l)
    return {"Premier League": "eng.1", "LaLiga": "esp.1", "Bundesliga": "ger.1", "Serie A": "ita.1", "Ligue 1": "fra.1"}.get(b)

def _market_grade(r):
    if not r: return None
    return {"grade": counts.grade(r["baseline_log_loss"] - r["log_loss"]), "log_loss_gain": round(r["baseline_log_loss"] - r["log_loss"], 4), "n": r["n"]}

def _grade_rec(r): return {"grade": r["grade"], "log_loss_gain": r["log_loss_gain"], "n": r["n"]} if r else None

def get_grades(lg, hist, fit_hist, ref):
    """درجات الموثوقية تُحسب تدريجيًا في الخلفية: كل سوق يجهز فور انتهائه، وفشل سوق لا يوقف البقية، ولا ننتظر انتهاء تحميل الإحصاءات."""
    now = time.time()
    with _GL:
        rec = _grades.get(lg)
        stale = rec and rec["state"] != "running" and (rec["until"] < now or len(xgmod.STATS) > rec["stamp"] * 1.3 + 10)
        if rec and not stale: return rec["g"]
        rec = {"g": dict(rec["g"]) if rec else {}, "state": "running", "until": now + 24 * 3600, "stamp": len(xgmod.STATS), "finished": 0, "total": 1 + len(counts.METRICS), "errors": []}
        _grades[lg] = rec
    def work():
        def step(name, fn):
            try: fn()
            except Exception as e:
                traceback.print_exc(); rec["errors"].append(f"{name}: {type(e).__name__}"); rec["g"][name] = None
            rec["finished"] += 1
        def goals():
            me = backtest.market_eval(hist, xi=0.0019, use_dc=True); rec["g"]["goals"] = _market_grade(me.get("over25")); rec["g"]["btts"] = _market_grade(me.get("btts"))
        step("goals", goals)
        for k in ("corners", "sot", "shots", "yellow", "fouls", "corners_1h", "corners_2h"):   # الأهم أولًا
            def one(k=k):
                tot, rc = counts.evaluate_all(counts.rows_for(fit_hist, k, ref), with_race=k in ("corners", "corners_1h", "corners_2h", "shots", "sot"))
                rec["g"][k] = _grade_rec(tot)
                if k in ("corners", "corners_1h", "corners_2h", "shots", "sot"): rec["g"][k + "_race"] = _grade_rec(rc)
            step(k, one)
        rec["state"] = "done"
    if BLOCK_TUNE: work()
    else: threading.Thread(target=work, daemon=True).start()
    return rec["g"]

def grades_state(lg):
    rec = _grades.get(lg)
    return {"state": rec["state"], "finished": rec["finished"], "total": rec["total"], "errors": rec["errors"][:3]} if rec else None

def _verdict(p, base, grade, ready=True, mp=None):
    """هل أختار هذا الحدث؟ قاعدة معلنة: لا اعتماد على سوق لم يتفوق في الاختبار، ولا «نعم» إن خالفها السوق بفارق كبير."""
    if not ready: return {"call": "انتظر", "why": "جارٍ اختبار موثوقية هذا السوق على مباريات سابقة، أعد النظر بعد قليل"}
    if grade is None: return {"call": "غير مؤكد", "why": "لم يُختبر هذا السوق تاريخيًا (بياناته غير كافية للاختبار)"}
    if grade == "ضعيف": return {"call": "لا", "why": "النموذج لا يتفوق على المتوسط التاريخي في هذا السوق، فلا أعتمد على احتماله"}
    need = 62 if grade == "جيد" else 65
    if p >= need: call, why = "نعم", f"احتمال مرتفع في سوق اجتاز الاختبار ({grade})"
    elif p >= 55: call, why = "محايد", "ميل خفيف فقط ولا يكفي للاعتماد عليه"
    elif p >= 50: call, why = "لا", "الاحتمال قريب من التساوي، لا ميزة واضحة"
    else: call, why = "لا", "الأرجح ألا يحدث"
    if base is not None and abs(p - base) < 2: why += "؛ ولا يختلف عن المعتاد تاريخيًا"
    if mp is not None:
        gap = p - mp
        if call == "نعم" and abs(gap) >= 8:
            call, why = "محايد", f"السوق يقدّره {mp}% (فارق {abs(gap):.0f} نقطة عن النموذج)؛ فارق كبير كهذا غالبًا يعني معلومة ينقصنا، فلا أعتمد عليه"
        else: why += f"؛ السوق يقدّره {mp}% (فارق {gap:+.1f})"
    return {"call": call, "why": why}

def _market_probs(mk, hn, an):
    """احتمالات السوق (بلا هامش) لأحداث نعرف سعرها: نتيجة المباراة وأكثر/أقل من خط الأهداف."""
    if not mk: return {}
    out = {}; ml = mk.get("ml")
    if ml:
        h, d, a = ml["prob"]["home"], ml["prob"]["draw"], ml["prob"]["away"]
        out.update({f"فوز {hn}": h, "تعادل": d, f"فوز {an}": a, f"{hn} أو تعادل": round(h + d, 1), f"{an} أو تعادل": round(a + d, 1), "أحد الفريقين يفوز (لا تعادل)": round(h + a, 1)})
    t = mk.get("total")
    if t: out.update({f"المجموع أكثر من {t['line']}": t["over"]["prob"], f"المجموع أقل من {t['line']}": t["under"]["prob"]})
    return out

def _build_events(metrics, grades, pr, par, fit_hist, hn, an, mprob=None, gstate="done"):
    running = gstate == "running"; G = grades or {}; gr = lambda k: (G.get(k) or {}).get("grade"); ev, groups = [], []; mprob = mprob or {}
    def add(g, label, pp, base, grade, n, gk=None):
        mp = mprob.get(label) if g in ("result", "goals") else None
        rd = True if g == "result" else ((gk or g) in G or not running)          # «انتظر» فقط للسوق الذي لم يُختبر بعد
        ev.append({"group": g, "label": label, "p": round(pp, 1), "base": None if base is None else round(base, 1), "grade": grade, "n": n, "mp": mp, **_verdict(pp, base, grade, rd, mp)})
    n = len(fit_hist); share = lambda f: round(100 * sum(1 for h in fit_hist if f(h)) / n, 1) if n else None
    if pr:
        mk = pr["markets"]; gain = (par["baseline_log_loss"] - par["log_loss"]) if par.get("baseline_log_loss") and par.get("log_loss") else None
        g1 = counts.grade(gain) if gain is not None and par.get("status") == "tuned" else None
        groups.append({"key": "result", "label": "نتيجة المباراة", "available": True, "expected": None})
        for lab, v, f in ((f"فوز {hn}", pr["home"], lambda h: h[3] > h[4]), ("تعادل", pr["draw"], lambda h: h[3] == h[4]), (f"فوز {an}", pr["away"], lambda h: h[3] < h[4]),
                          (f"{hn} أو تعادل", mk["double_chance"]["1X"], lambda h: h[3] >= h[4]), (f"{an} أو تعادل", mk["double_chance"]["X2"], lambda h: h[3] <= h[4]),
                          ("أحد الفريقين يفوز (لا تعادل)", mk["double_chance"]["12"], lambda h: h[3] != h[4])):
            add("result", lab, v, share(f), g1, n)
        gm = metrics["goals"]; groups.append({"key": "goals", "label": "الأهداف", "available": True, "expected": {"home": gm["exp_home"], "away": gm["exp_away"], "total": gm["exp_total"]}})
        for x in gm["lines"]:
            add("goals", f"المجموع أكثر من {x['line']}", x["over"], x["base"], gr("goals"), n); add("goals", f"المجموع أقل من {x['line']}", 100 - x["over"], 100 - x["base"], gr("goals"), n)
        for sd, nm in (("home", hn), ("away", an)):
            for x in gm["team_lines"][sd]:
                add("goals", f"{nm} يسجل أكثر من {x['line']}", x["over"], x["base"], gr("goals"), n); add("goals", f"{nm} يسجل أقل من {x['line']}", 100 - x["over"], 100 - x["base"], gr("goals"), n)
        add("goals", "كلا الفريقين يسجل: نعم", mk["btts"]["yes"], share(lambda h: h[3] > 0 and h[4] > 0), gr("btts"), n, gk="btts")
        add("goals", "كلا الفريقين يسجل: لا", mk["btts"]["no"], share(lambda h: h[3] == 0 or h[4] == 0), gr("btts"), n, gk="btts")
        add("goals", f"{hn} شباكه نظيفة", mk["clean_sheet"]["home"], share(lambda h: h[4] == 0), gr("goals"), n)
        add("goals", f"{an} شباكه نظيفة", mk["clean_sheet"]["away"], share(lambda h: h[3] == 0), gr("goals"), n)
    for k, r in metrics.items():
        if k == "goals": continue
        if not r.get("available"):
            groups.append({"key": k, "label": r["label"], "available": False, "reason": r.get("reason"), "expected": None}); continue
        groups.append({"key": k, "label": r["label"], "available": True, "expected": {"home": r["exp_home"], "away": r["exp_away"], "total": r["exp_total"]}})
        for x in r["lines"]:
            add(k, f"المجموع أكثر من {x['line']}", x["over"], x["base"], gr(k), r["n_rows"]); add(k, f"المجموع أقل من {x['line']}", 100 - x["over"], 100 - x["base"], gr(k), r["n_rows"])
        for sd, nm in (("home", hn), ("away", an)):
            for x in r["team_lines"][sd]:
                add(k, f"{nm} أكثر من {x['line']}", x["over"], x["base"], gr(k), r["n_rows"]); add(k, f"{nm} أقل من {x['line']}", 100 - x["over"], 100 - x["base"], gr(k), r["n_rows"])
        if k in ("corners", "corners_1h", "corners_2h", "shots", "sot") and r.get("race"):
            rc = r["race"]
            add(k, f"{hn} الأكثر عددًا", rc["home"], rc["base"]["home"], gr(k + "_race"), r["n_rows"], gk=k + "_race"); add(k, "تعادل في العدد", rc["draw"], rc["base"]["draw"], gr(k + "_race"), r["n_rows"], gk=k + "_race")
            add(k, f"{an} الأكثر عددًا", rc["away"], rc["base"]["away"], gr(k + "_race"), r["n_rows"], gk=k + "_race")
    return ev, groups

def _board_match(match_id):
    m = pc.get(f"/v1/matches/{match_id}", ttl=300); ref = date.fromisoformat(m["date"]); lg = m["league"]["id"]
    hid, aid, hn, an = m["home_team"]["id"], m["away_team"]["id"], m["home_team"]["name"], m["away_team"]["name"]
    leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]; hist, fit_hist = _fit_hist(m, leagues, ref)
    me_ = next((l for l in leagues if l["id"] == lg), None)
    if me_ and _espn_slug(me_): xgmod.SLUG[lg] = _espn_slug(me_)
    st = xgmod.ensure(lg, fit_hist, ref); p = predict_match(match_id); pr = p.get("prediction")
    metrics = {}
    if pr:
        n = len(fit_hist); tot = [h[3] + h[4] for h in fit_hist]; hv = [h[3] for h in fit_hist]; av = [h[4] for h in fit_hist]
        mk = pr["markets"]
        metrics["goals"] = {"available": True, "label": "الأهداف", "exp_home": pr["xg_home"], "exp_away": pr["xg_away"], "exp_total": round(pr["xg_home"] + pr["xg_away"], 2), "n_rows": n,
            "lines": [{"line": float(L), "over": mk["ou"][L]["over"], "base": round(100 * sum(1 for x in tot if x > float(L)) / n, 1)} for L in ("1.5", "2.5", "3.5")],
            "team_lines": {side: [{"line": float(L), "over": mk["team_goals"][side][L], "base": round(100 * sum(1 for x in (hv if side == "home" else av) if x > float(L)) / n, 1)} for L in ("0.5", "1.5", "2.5")] for side in ("home", "away")}}
    for k in counts.METRICS:
        r = counts.predict(fit_hist, k, hid, aid, ref)
        if r.get("available"):
            r["lines"] = [{**x, "over": round(x["over"] * 100, 1), "base": round(x["base"] * 100, 1)} for x in r["lines"]]
            r["team_lines"] = {sd: [{**x, "over": round(x["over"] * 100, 1), "base": round(x["base"] * 100, 1)} for x in v] for sd, v in r["team_lines"].items()}
            rc = r["race"]; r["race"] = {"home": round(rc["home"] * 100, 1), "draw": round(rc["draw"] * 100, 1), "away": round(rc["away"] * 100, 1),
                                         "base": {k: round(v * 100, 1) for k, v in rc["base"].items()}, "hist": rc["hist"]}
        metrics[k] = r
    grades = get_grades(lg, hist, fit_hist, ref); gs = grades_state(lg) or {"state": "done", "finished": 0, "total": 0, "errors": []}; picks = []
    if grades:
        for k, r in metrics.items():
            g = (grades.get(k) or {}).get("grade")
            if not r.get("available") or g not in ("جيد", "مقبول"): continue
            w = 1.0 if g == "جيد" else 0.7
            cands = [(f"المجموع {{}} {x['line']}", x["over"], x["base"]) for x in r["lines"]]
            for sd, nm in (("home", hn), ("away", an)): cands += [(f"{nm} {{}} {x['line']}", x["over"], x["base"]) for x in r["team_lines"][sd]]
            for t, ov, base in cands:
                side, pp, bb = ("أكثر من", ov, base) if ov >= 50 else ("أقل من", round(100 - ov, 1), round(100 - base, 1))
                if pp >= 58: picks.append({"market": r["label"], "text": t.format(side), "p": pp, "base": bb, "grade": g, "score": round((pp - 50) * w, 1)})
        for k in ("corners", "corners_1h", "corners_2h", "shots", "sot"):   # الفريق الأكثر بالعدد
            r = metrics.get(k); g = (grades.get(k + "_race") or {}).get("grade")
            if r and r.get("available") and g in ("جيد", "مقبول"):
                w = 1.0 if g == "جيد" else 0.7
                for sd, nm in (("home", hn), ("away", an)):
                    if r["race"][sd] >= 50: picks.append({"market": r["label"] + " (الأكثر)", "text": f"{nm} الأكثر", "p": r["race"][sd], "base": r["race"]["base"][sd], "grade": g, "score": round((r["race"][sd] - 50) * w, 1)})
        picks = sorted(picks, key=lambda x: -x["score"])[:10]
    if gs["state"] == "running" and not picks: picks = None
    try: mkt = market_facts(match_id)
    except Exception: mkt = None
    events, groups = _build_events(metrics, grades, pr, p.get("params") or {}, fit_hist, hn, an, _market_probs((mkt or {}).get("market"), hn, an), gs["state"])
    return {"match": {"id": m["id"], "home": hn, "away": an, "league": m["league"]["name"], "home_logo": m["home_team"].get("image_url"), "away_logo": m["away_team"].get("image_url")},
            "events": events, "groups": groups,
            "status": {"stats_loading": st["running"], "done": st["done"], "total": st["total"], "stats_matches": len(xgmod.STATS)},
            "metrics": metrics, "half_split": counts.half_shares(fit_hist, ref, hid, aid), "grades": grades or {}, "grades_state": gs, "picks": picks, "outlook": {k: pr[k] for k in ("home", "draw", "away")} if pr else None,
            "disclaimer": "احتمال مرتفع لا يعني رهانًا رابحًا: شركة الرهان تسعّر الاحتمال نفسه. القيمة تظهر فقط إن كان السعر أعلى من العادل. هذا تقدير إحصائي وليس ضمانًا ولا نصيحة رهان."}


# ---- ترتيب الدوري (محسوب من النتائج المنتهية في المصدر) ----
_STAT_KEYS = ("shots", "sot", "yellow", "corners", "fouls", "xg")

def standings(league_id, season=None):
    leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]
    lg = next((l for l in leagues if l["id"] == league_id), None)
    if not lg: raise pc.PitchError("RESOURCE_NOT_FOUND", "الدوري غير موجود", 404)
    seasons = sorted(lg.get("seasons") or [], reverse=True)
    if not seasons: raise pc.PitchError("RESOURCE_NOT_FOUND", "لا توجد مواسم متاحة لهذا الدوري", 404)
    sn = season if season in seasons else seasons[0]
    data = pc.get(f"/v1/leagues/{league_id}/matches", {"season": sn}, ttl=1800)
    z = lambda: {"p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0}
    def add(b, gf, ga):
        b["p"] += 1; b["gf"] += gf; b["ga"] += ga; r = "w" if gf > ga else "d" if gf == ga else "l"; b[r] += 1; return r
    T, games, hw = {}, [], {"h": 0, "d": 0, "a": 0}
    for m in data.get("matches", []):
        if m.get("score_home") is None or m.get("score_away") is None: continue
        h, a, gh, ga = m["home_team"], m["away_team"], m["score_home"], m["score_away"]; md = date.fromisoformat(m["date"])
        for t in (h, a):
            last50.NAMES[t["id"]] = t["name"]
            T.setdefault(t["id"], {"id": t["id"], "name": t["name"], "all": z(), "home": z(), "away": z(), "res": []})
        rh = add(T[h["id"]]["all"], gh, ga); add(T[h["id"]]["home"], gh, ga); ra = add(T[a["id"]]["all"], ga, gh); add(T[a["id"]]["away"], ga, gh)
        T[h["id"]]["res"].append((md, rh.upper())); T[a["id"]]["res"].append((md, ra.upper()))
        hw["h" if gh > ga else "d" if gh == ga else "a"] += 1; games.append((md, h["id"], a["id"], gh, ga, m["id"]))
    ref = date.today() + timedelta(days=1)
    if _espn_slug(lg): xgmod.SLUG[league_id] = _espn_slug(lg)
    st = xgmod.ensure(league_id, games, ref) if games else {"running": False, "done": 0, "total": 0}
    agg = {t: {k: [0.0, 0.0, 0] for k in _STAT_KEYS} for t in T}
    for md, h, a, gh, ga, mid in games:
        ms = xgmod.STATS.get(mid)
        if not ms: continue
        for k, (x, y) in ms.items():
            if k in agg[h]:
                agg[h][k][0] += x; agg[h][k][1] += y; agg[h][k][2] += 1; agg[a][k][0] += y; agg[a][k][1] += x; agg[a][k][2] += 1
    pts = lambda b: 3 * b["w"] + b["d"]
    rows = sorted(T.values(), key=lambda r: (-pts(r["all"]), -(r["all"]["gf"] - r["all"]["ga"]), -r["all"]["gf"], r["name"]))
    table = []
    for i, r in enumerate(rows, 1):
        table.append({"pos": i, "id": r["id"], "name": r["name"], **{k: {**r[k], "pts": pts(r[k]), "gd": r[k]["gf"] - r[k]["ga"]} for k in ("all", "home", "away")},
                      "form": [x[1] for x in sorted(r["res"])[-5:]],
                      "stats": {k: {"for": round(v[0] / v[2], 2), "against": round(v[1] / v[2], 2), "n": v[2]} for k, v in agg[r["id"]].items() if v[2]}})
    n = len(games)
    return {"league": {"id": lg["id"], "name": lg["name"], "logo": lg.get("image_url")}, "season": sn, "seasons": seasons, "played": n,
            "through": max((g[0] for g in games), default=None) and max(g[0] for g in games).isoformat(),
            "summary": {"goals_per_match": round(sum(g[3] + g[4] for g in games) / n, 2) if n else None,
                        "home_win_pct": round(100 * hw["h"] / n, 1) if n else None, "draw_pct": round(100 * hw["d"] / n, 1) if n else None, "away_win_pct": round(100 * hw["a"] / n, 1) if n else None},
            "stats_loading": st["running"], "stats_done": st["done"], "stats_total": st["total"], "table": table,
            "note": "الترتيب محسوب من النتائج المنتهية في المصدر (نقاط، فارق الأهداف، الأهداف المسجلة، ثم الاسم)، وقد يختلف عن الترتيب الرسمي إن اختلفت معايير فض التعادل أو عُدّلت نقاط. متوسطات البطاقات والركنيات والتسديدات من إحصاءات المباريات المتوفرة فقط، والعدد n يوضح عدد المباريات المحسوبة."}


# ---- تخزين مؤقت للنتائج الثقيلة (يمنع إعادة الحساب مع كل تحديث للصفحة) ----
_PM, _BM, _FM = cache.TTLCache(150), cache.TTLCache(150), cache.TTLCache(60)

def predict_match(match_id):
    settled = lambda v: 600 if (v.get("params") or {}).get("status") == "tuned" and not (v.get("xg") or {}).get("loading") else 20
    return _PM.get_or(match_id, settled, lambda: _predict_match(match_id))

def board_match(match_id):
    settled = lambda v: 20 if v["status"]["stats_loading"] or (v.get("grades_state") or {}).get("state") == "running" else 180
    return _BM.get_or(match_id, settled, lambda: _board_match(match_id))

def clear_caches():
    _PM.clear(); _BM.clear(); _FM.clear(); counts._CM.clear(); _MK.clear()

def peek_board(match_id):
    """لوحة الأسواق إن كانت جاهزة في الذاكرة فقط (بلا حساب جديد)."""
    hit = _BM.d.get(match_id)
    return hit[1] if hit and hit[0] > time.time() else None


# ---- السوق والملعب (ESPN) ----
_MK = cache.TTLCache(100)

def market_facts(match_id):
    def run():
        m = pc.get(f"/v1/matches/{match_id}", ttl=300); leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]
        me = next((l for l in leagues if l["id"] == m["league"]["id"]), None); slug = _espn_slug(me) if me else None
        if not slug: return None
        e = espn.find_event(slug, date.fromisoformat(m["date"]), m["home_team"]["name"], m["away_team"]["name"])
        return espn.facts(e) if e else None
    return _MK.get_or(match_id, lambda v: 600 if v else 120, run)

def market_view(match_id):
    p = predict_match(match_id); pr = p.get("prediction"); m = p["match"]; f = market_facts(match_id)
    out = {"match": {"home": m["home"], "away": m["away"]}, "available": bool(f), "venue": (f or {}).get("venue"), "form": (f or {}).get("form"), "records": (f or {}).get("records"), "market": None, "compare": []}
    if not f: out["reason"] = "ESPN لا يغطي هذا الدوري أو لم أجد المباراة هناك (الأسعار متاحة للمباريات القادمة في الدوريات الخمسة الكبرى ودوري الأبطال)"; return out
    mk = f.get("market"); out["market"] = mk
    if not mk: out["reason"] = "لا توجد أسعار منشورة لهذه المباراة بعد"; return out
    if pr and mk.get("ml"):
        for lab, mine, theirs in ((f"فوز {m['home']}", pr["home"], mk["ml"]["prob"]["home"]), ("تعادل", pr["draw"], mk["ml"]["prob"]["draw"]), (f"فوز {m['away']}", pr["away"], mk["ml"]["prob"]["away"])):
            out["compare"].append({"label": lab, "model": mine, "market": theirs, "gap": round(mine - theirs, 1)})
    t = mk.get("total")
    if pr and t:
        ou = pr["markets"]["ou"].get(str(t["line"]))
        if ou:
            out["compare"] += [{"label": f"المجموع أكثر من {t['line']}", "model": ou["over"], "market": t["over"]["prob"], "gap": round(ou["over"] - t["over"]["prob"], 1)},
                               {"label": f"المجموع أقل من {t['line']}", "model": ou["under"], "market": t["under"]["prob"], "gap": round(ou["under"] - t["under"]["prob"], 1)}]
    big = [c for c in out["compare"] if abs(c["gap"]) >= 8]
    out["read"] = ("النموذج يخالف السوق بفارق كبير في: " + "، ".join(c["label"] for c in big) + ". السوق عادةً أدق، فالأرجح أن ينقصنا خبر (إصابة/تشكيلة). لا تعتمد على هذا الفارق.") if big else "النموذج متقارب مع السوق، وهذا يزيد الثقة بالتقدير."
    out["source"] = (mk.get("provider") or "شركة رهان") + " عبر ESPN (أسعار إغلاق، بعد إزالة الهامش)"
    return out


def data_status(league_id):
    """فحص صريح لجاهزية البيانات في دوري: ما المتوفر، ومن أي مصدر، وما الناقص ولماذا."""
    leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]; lg = next((l for l in leagues if l["id"] == league_id), None)
    if not lg: raise pc.PitchError("RESOURCE_NOT_FOUND", "الدوري غير موجود", 404)
    hist = history(league_id, _all_seasons(leagues, league_id), date.today() + timedelta(days=1)); n = len(hist); ids = [h[5] for h in hist]
    slug = _espn_slug(lg)
    if slug: xgmod.SLUG[league_id] = slug
    st = xgmod.state(league_id); names = {"shots": "التسديدات", "sot": "التسديدات على المرمى", "yellow": "البطاقات الصفراء", "corners": "الركنيات", "fouls": "الأخطاء", "xg": "xG", "corners_1h": "ركنيات الشوط الأول", "corners_2h": "ركنيات الشوط الثاني"}
    why = {"corners": "لا يوفرها PitchAPI" + ("؛ يكمّلها ESPN لهذا الدوري عند توفرها في مبارياته" if slug else "؛ وESPN لا يغطي هذا الدوري، فلا مصدر لها حاليًا"),
           "corners_1h": "المصدر لا يفصل الشوطين غالبًا", "corners_2h": "المصدر لا يفصل الشوطين غالبًا"}
    metrics = {}
    for k, label in names.items():
        c = sum(1 for i in ids if k in xgmod.STATS.get(i, {})); ok = c >= counts.MIN_ROWS
        metrics[k] = {"label": label, "n": c, "pct": round(100 * c / n) if n else 0, "usable": ok or (k == "xg" and n and c / n >= 0.6),
                      "why": None if ok else ("جارٍ التحميل" if st["running"] else why.get(k, "إحصاءات غير كافية في المصدر"))}
    return {"league": {"id": lg["id"], "name": lg["name"]}, "matches": n, "last_match": max((h[0] for h in hist), default=None) and max(h[0] for h in hist).isoformat(),
            "espn": {"covered": bool(slug), "slug": slug, "filled": st.get("espn_filled")}, "loading": st["running"], "done": st["done"], "total": st["total"],
            "min_rows": counts.MIN_ROWS, "metrics": metrics, "market_source": "ESPN (DraftKings) للمباريات القادمة" if slug else None,
            "note": "المطلوب 120 مباراة على الأقل لكل إحصائية كي يُبنى نموذجها. تُحمَّل الإحصاءات في الخلفية من الأحدث للأقدم."}
