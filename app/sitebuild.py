"""يبني موقعًا ثابتًا (تطبيق PWA) من نتائج المحرك الإحصائي: يعمل على GitHub Actions ثم يُنشر على GitHub Pages.
الهاتف يفتح ملفات JSON جاهزة فقط، فلا خادم ولا حساب ثقيل عليه. التشغيل: PITCHAPI_KEY=... python -m app.sitebuild"""
import os, sys, json, time, shutil, zlib, struct, traceback, requests
from datetime import date, datetime, timedelta, timezone
from . import pitch_client as pc, service, persist, xg as xgmod

OUT = os.environ.get("SITE_DIR", "site")
DAYS = int(os.environ.get("SITE_DAYS", "5"))          # عدد الأيام القادمة المحسوبة مسبقًا
MAX_MATCHES = int(os.environ.get("SITE_MAX", "450"))
MAX_T2 = int(os.environ.get("SITE_MAX_T2", "200"))      # سقف مباريات بقية الدوريات (نتائج فقط)   # سقف المباريات التفصيلية
BUDGET = int(os.environ.get("SITE_BUDGET_MIN", "25")) * 60
PREV = os.environ.get("SITE_PREV_URL", "https://blawte-dot.github.io/Fooooot/")   # النسخة المنشورة سابقًا: احتياط لأي ملف يفشل بناؤه الآن
DONE = {"finished", "ft", "final", "completed", "ended"}

def w(rel, obj):
    p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

def png(size, rgb=(14, 22, 38), dot=(61, 220, 151)):
    """أيقونة PNG بسيطة (خلفية داكنة ودائرة خضراء) بلا مكتبات خارجية."""
    cx = cy = size / 2; r2 = (size * 0.28) ** 2; rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size): row += bytes(dot if (x - cx) ** 2 + (y - cy) ** 2 <= r2 else rgb)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)
    def ch(t, d): c = struct.pack(">I", len(d)) + t + d; return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + ch(b"IDAT", raw) + ch(b"IEND", b"")

def keep_prev(rel):
    """يعيد استخدام الملف المنشور سابقًا إن وُجد (فلا تختفي مباراة بسبب فشل مؤقت)."""
    try:
        r = requests.get(PREV + rel, timeout=20)
        if r.status_code == 200 and r.content[:1] in (b"{", b"["):
            p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(r.content); return True
    except Exception: pass
    return False

def retry(fn, mid, tries=4):
    for k in range(tries):
        try: return fn(mid)
        except Exception as e:
            if k < tries - 1 and ("too many" in str(e).lower() or "429" in str(e)): time.sleep(20 * (k + 1)); continue
            raise

AI_V = 2
AI_MAX = int(os.environ.get("SITE_AI_MAX", "6"))            # سقف طلبات Gemini في التشغيل الواحد (الحصة المجانية محدودة)
AI_TTL = {"ai": 8, "ai-news": 8, "ai-dossier": 24}            # ساعات الاحتفاظ بنتيجة سابقة قبل إعادة الحساب

def prev_fresh(rel, hours):
    """نتيجة Gemini المنشورة سابقًا إن كانت أحدث من المدة المحددة: تُعاد بدل استهلاك طلب جديد."""
    try:
        r = requests.get(PREV + rel, timeout=20)
        if r.status_code != 200: return False
        j = r.json()
        if j.get("_v") != AI_V: return False          # نتائج الإصدار القديم كررت عبارة خاطئة عن Elo وxG
        g = datetime.fromisoformat(j.get("_gen", "1970-01-01T00:00:00+00:00"))
        if datetime.now(timezone.utc) - g < timedelta(hours=hours):
            p = os.path.join(OUT, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(r.content); return True
    except Exception: pass
    return False

def ai_phase(todo, stats, log, deadline, mdate=None):
    """يحسب رأي Gemini (تحليل، أخبار، دراسة شاملة) للمباريات الأقرب أولًا، ضمن سقف الطلبات. المفتاح من GEMINI_API_KEY (سر GitHub) ولا يظهر في الموقع."""
    if not os.environ.get("GEMINI_API_KEY"): log("ai: no key, skipped"); return
    os.environ.setdefault("AI_PER_IP_HOUR", "100000"); os.environ.setdefault("AI_GLOBAL_HOUR", "100000")
    from . import ai
    try: stats["ai_models"] = ai.list_models()[:40]
    except Exception as e:
        log(f"ai models: {e}")
        if len(stats["errors"]) < 40: stats["errors"].append(f"ai-models: {str(e)[:170]}")
    used = 0; stats["ai"] = {"new": 0, "reused": 0, "fail": 0}; blocked = set()
    mids = [m for m in todo if os.path.exists(os.path.join(OUT, f"api/predict/{m}.json"))]
    if mdate: mids.sort(key=lambda m: str(mdate.get(m) or "9"))      # الأقرب موعدًا أولًا (الفرز ثابت فيحفظ الترتيب داخل اليوم)
    for name in ("ai", "ai-news", "ai-dossier"):            # التحليل أولًا لكل المباريات (الأرخص)، ثم الأخبار، ثم الدراسة الشاملة
        for mid in mids:
            if used >= AI_MAX or time.time() > deadline or name in blocked: break
            rel = f"api/{name}/{mid}.json"
            if prev_fresh(rel, AI_TTL[name]): stats["ai"]["reused"] += 1; continue
            try: service.board_match(mid)        # يضمن وجود لوحة الأسواق في الذاكرة ليستفيد منها التحليل
            except Exception: pass
            for attempt in (1, 2):
                try:
                    if name == "ai-dossier":
                        res = ai.dossier(mid, "ci"); t1 = time.time()
                        while res.get("state") == "running" and time.time() - t1 < 240: time.sleep(6); res = ai.dossier(mid, "ci")
                        if res.get("state") != "done": raise RuntimeError(res.get("error") or "dossier not finished")
                    elif name == "ai": res = ai.analyze(mid, "ci")
                    else: res = ai.news(mid, "ci")
                    w(rel, {**res, "_v": AI_V, "_gen": datetime.now(timezone.utc).isoformat(timespec="seconds")}); used += 1; stats["ai"]["new"] += 1; time.sleep(15); break
                except Exception as e:
                    code = getattr(e, "code", "")
                    if code == "AI_RATE_LIMIT" and attempt == 1: log("ai: rate limited, waiting 75s"); time.sleep(75); continue
                    used += 1; stats["ai"]["fail"] += 1; msg = f"{name} {mid}: {type(e).__name__}: {str(e)[:160]}"; log(msg)
                    if len(stats["errors"]) < 40: stats["errors"].append(msg)
                    keep_prev(rel)
                    if code == "AI_AUTH": log("ai: key rejected, stopping"); return
                    if code == "AI_RATE_LIMIT": log("ai: quota exhausted, stopping all Gemini calls this run"); return
                    break

X_LAST50, X_TEAM50 = int(os.environ.get("SITE_LAST50_MAX", "3")), int(os.environ.get("SITE_TEAM50_MAX", "4"))

def _cov(res):
    """نسبة المباريات التي لها إحصاءات تفصيلية: تمنع نشر نتيجة ناقصة بسبب فشل مؤقت في الجلب."""
    sides = [res.get("home"), res.get("away")] if "home" in res else [res.get("data")]; out = []
    for d in sides:
        if not d or not d.get("n"): return 0
        out.append(max([m["n"] for k, m in (d.get("metrics") or {}).items() if k != "goals"] or [0]) / d["n"])
    return min(out) if out else 0

def teams_index():
    service.BLOCK_INDEX = True; service._idx.update(state="idle", teams={}, leagues=[], error=None); service._start_index()
    if service._idx["state"] != "ready": raise RuntimeError(service._idx.get("error") or "فشل بناء فهرس الفرق")
    byid = {l["id"]: l for l in service._idx["leagues"]}
    lab = lambda e: next((service.big5_label(byid[l]) for l in e["comps"] if l in byid and service.big5_label(byid[l])), None)
    def ids(e): o = sorted(e["comps"], key=e["comps"].get, reverse=True); return o[:5] + [l for l in o[5:] if l in byid and service.is_ucl(byid[l])]
    out = [{"id": tid, "name": e["name"], "competitions": [byid[l]["name"] for l in ids(e)[:3] if l in byid], "comp_ids": ids(e), "big5": lab(e), "n": sum(e["comps"].values())}
           for tid, e in service._idx["teams"].items()]
    if sum(1 for t in out if t["big5"]) < 90: raise RuntimeError("فهرس الفرق ناقص (أقل من 90 فريقًا من الخمس الكبرى)")
    return {"teams": out}

def extras_phase(todo, mteams, target, leagues, tier, have, logos, stats, log, deadline):
    """الدوريات (ترتيب، قادمة، حالة البيانات، أداء) + فهرس الفرق + آخر 50 للمباريات والفرق. كل شيء تدريجي ومحفوظ، فلا يتكرر الحساب الثقيل."""
    x = stats["x"] = {"new": 0, "reused": 0, "fail": 0}; now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    def cached(rel, hours, fn, key):
        if hours and prev_fresh(rel, hours): x["reused"] += 1; return True
        if time.time() > deadline: keep_prev(rel); return False
        try: res = retry(lambda _x: fn(), None); w(rel, {**res, "_gen": now()}); x["new"] += 1; return True
        except Exception as e:
            x["fail"] += 1; msg = f"{key}: {type(e).__name__}: {str(e)[:160]}"; log(msg)
            if len(stats["errors"]) < 40: stats["errors"].append(msg)
            keep_prev(rel); return False
    w("api/logos.json", logos)
    for l in leagues:
        lid = l["id"]
        if lid not in target: continue
        cached(f"api/standings/{lid}.json", 0, lambda: service.standings(lid), f"standings {lid}")
        cached(f"api/league/{lid}/upcoming.json", 0, lambda: (lambda d: {"league": d.get("league"), "matches": d.get("matches", [])})(pc.get(f"/v1/leagues/{lid}/matches", {"status": "upcoming"}, ttl=600)), f"upcoming {lid}")
        if tier.get(lid, 2) <= 1 and lid in have:
            cached(f"api/data-status/{lid}.json", 6, lambda: service.data_status(lid), f"data-status {lid}")
            cached(f"api/backtest/{lid}.json", 12, lambda: service.backtest_league(lid), f"backtest {lid}")
    cached("api/teams.json", 24, teams_index, "teams-index")
    try: tix = {t["id"]: t for t in json.load(open(os.path.join(OUT, "api/teams.json"), encoding="utf-8"))["teams"]}
    except Exception: tix = {}
    n = 0
    for mid in todo:
        if n >= X_LAST50 or time.time() > deadline: break
        rel = f"api/last50/{mid}.json"
        if prev_fresh(rel, 24): x["reused"] += 1; continue
        def run(mid=mid):
            res = service.last50_match(mid)
            if _cov(res) < 0.4: raise RuntimeError("إحصاءات ناقصة (ربما فشل جلب مؤقت)")
            return res
        cached(rel, 0, run, f"last50 {mid}"); n += 1; time.sleep(4)
    seen, n = set(), 0
    for mid in todo:
        for tid, nm in mteams.get(mid, []):
            if not tid or tid in seen or tid not in tix: continue
            seen.add(tid); rel = f"api/team50/{tid}.json"
            if prev_fresh(rel, 24): x["reused"] += 1; continue
            if n >= X_TEAM50 or time.time() > deadline: continue
            def run(tid=tid, nm=nm):
                res = service.team50(tid, "main", tix[tid]["comp_ids"], nm)
                if _cov(res) < 0.4: raise RuntimeError("إحصاءات ناقصة (ربما فشل جلب مؤقت)")
                return res
            cached(rel, 0, run, f"team50 {tid}"); n += 1; time.sleep(4)

def wait_board(mid, deadline):
    while True:
        r = service.board_match(mid)
        busy = r["status"]["stats_loading"] or (r.get("grades_state") or {}).get("state") == "running"
        if not busy or time.time() > deadline: return r
        time.sleep(15)

ARCH = os.environ.get("ARCH_DIR", "data/archive"); RECENT_MAX = int(os.environ.get("SITE_RECENT_MAX", "60"))

def _jl(path, default):
    try: return json.load(open(path, encoding="utf-8"))
    except Exception: return default

def _out(rel): return _jl(os.path.join(OUT, rel), None)

def _load_arch():
    """لقطات التوقع: ملف لكل يوم مباراة (s-YYYY-MM-DD.json) فلا يُعاد التزام الأرشيف كله مع كل تغيير. يدعم الملف القديم الواحد ويدمجه."""
    import glob
    d = {}; old = _jl(os.path.join(ARCH, "snapshots.json"), None)
    if isinstance(old, dict): d.update(old)
    for f in sorted(glob.glob(os.path.join(ARCH, "s-*.json"))): d.update(_jl(f, {}))
    return d

def _save_arch(archive):
    import glob
    os.makedirs(ARCH, exist_ok=True); by = {}
    for k, v in archive.items(): by.setdefault((str(v.get("date") or v.get("ts") or "unknown")[:10]), {})[k] = v
    keep = set()
    for day, items in by.items():
        f = os.path.join(ARCH, f"s-{day}.json"); keep.add(f); new = json.dumps(items, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        try: old = open(f, encoding="utf-8").read()
        except OSError: old = None
        if old != new: open(f, "w", encoding="utf-8").write(new)
    for f in glob.glob(os.path.join(ARCH, "s-*.json")):
        if f not in keep: os.remove(f)
    legacy = os.path.join(ARCH, "snapshots.json")
    if os.path.exists(legacy): os.remove(legacy)

def recent_phase(todo, recent, mdate, mtier, archive, tr, stats, log, deadline):
    """قبل/أثناء/بعد: لقطات التوقع قبل الانطلاق، حالة مباشرة، ومراجعة بعد النهاية مقابل ما حدث فعلًا + سجل أداء تراكمي."""
    from . import review, last50
    ph = stats["phase"] = {"snap_new": 0, "post": 0, "live": 0, "reused": 0, "nosnap": 0, "retro": 0, "tr_added": 0}
    strip = lambda d: {k: v for k, v in d.items() if k != "ts"}
    for mid in todo:                                   # لقطات ما قبل المباراة (المباريات التي لم تبدأ فقط، ولا تُعدَّل بعد البداية)
        if mtier.get(mid, 2) > 1: continue
        p = _out(f"api/predict/{mid}.json")
        if not p: continue
        sn = review.snapshot(p, _out(f"api/board/{mid}.json"), mdate.get(mid))
        if sn and not (archive.get(mid) and strip(archive[mid]) == strip(sn) and not archive[mid].get("retro")): archive[mid] = sn; ph["snap_new"] += 1
    order = sorted(recent.items(), key=lambda kv: (review.phase_of(kv[1]) != "live", mtier.get(kv[0], 2)))[:RECENT_MAX]
    for mid, m in order:
        if time.time() > deadline: log("phase: deadline"); break
        for name in ("predict", "market", "last7", "last10", "board", "ai"):          # نسخة ما قبل المباراة تبقى متاحة بعد انطلاقها
            if not os.path.exists(os.path.join(OUT, f"api/{name}/{mid}.json")): keep_prev(f"api/{name}/{mid}.json")
        snap = archive.get(mid)
        if not snap:                                     # لا لقطة محفوظة (بداية النظام): تقدير بأثر رجعي يُعلَّم ولا يدخل السجل
            p = _out(f"api/predict/{mid}.json")
            if not p:
                try: p = service.predict_match(mid); w(f"api/predict/{mid}.json", p)
                except Exception: p = None
            snap = review.snapshot(p, _out(f"api/board/{mid}.json"), m.get("date")) if p else None
            if snap: snap["retro"] = True; ph["retro"] += 1
        kind = review.phase_of(m); rel = f"api/phase/{mid}.json"; hid, aid = (m.get("home_team") or {}).get("id"), (m.get("away_team") or {}).get("id")
        if kind == "post":
            if keep_prev(rel):
                pj = _out(rel)
                if pj and pj.get("state") == "post" and (pj.get("stats_ok") or pj.get("no_snapshot")): ph["reused"] += 1; continue
            if not snap: w(rel, {"state": "post", "score": [m["score_home"], m["score_away"]], "no_snapshot": True}); ph["nosnap"] += 1; continue
            try: stt = retry(lambda _x: last50._match_stats(mid, hid, aid), None)
            except Exception: stt = {}
            rev = review.review(snap, m, stt); w(rel, {**rev, "_gen": review.now()}); ph["post"] += 1
            need_stats = any(x["group"] not in ("goals", "result") for x in rev["picks"])
            if (rev["stats_ok"] or not need_stats) and review.update_tr(tr, mid, rev, snap): ph["tr_added"] += 1
        elif kind == "pending": w(rel, {"state": "pending", "ts": review.now()}); ph["pending"] = ph.get("pending", 0) + 1
        elif kind == "live":
            try: evs = pc.get(f"/v1/matches/{mid}/events", ttl=60).get("events") or []
            except Exception: evs = []
            st_ = review.live_state(m, snap, evs); w(rel, st_); ph["live"] += 1
            stats.setdefault("live_sample", {"keys": sorted(m.keys()), "status": m.get("status"), "minute": st_.get("minute"), "src": st_.get("minute_src"), "event_keys": sorted((evs[0] or {}).keys()) if evs else None})
    fin = next((m for m in recent.values() if review.phase_of(m) == "post"), None)
    if fin: stats["post_sample_keys"] = sorted(fin.keys())
    w("api/trackrecord.json", review.tr_view(tr))
    keep = set(todo) | set(recent); cut = (datetime.now(timezone.utc).date() - timedelta(days=21)).isoformat()
    pruned = {k: v for k, v in archive.items() if k in keep or str(v.get("date") or v.get("ts") or "9999")[:10] >= cut}
    _save_arch(pruned)
    os.makedirs(ARCH, exist_ok=True)
    json.dump(tr, open(os.path.join(ARCH, "trackrecord.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def main(log=print):
    t0 = time.time(); persist.load()
    leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]
    wl = [l for l in leagues if service.wanted_league(l)]; tier = {l["id"]: service.league_tier(l) for l in wl}; target = set(tier)
    w("api/leagues.json", {"leagues": [{**l, "big5": service.big5_label(l), "ucl": service.is_ucl(l), "famous": service.is_famous_league(l), "tier": tier[l["id"]]} for l in wl]})
    have = set(persist._docs([persist.BUNDLED]).keys())
    _ens, _gp = xgmod.ensure, service.get_params
    def ensure_g(lg, hist, ref, *a, **k):      # لا نجلب إحصاءات لدوري لم تُجهَّز بياناته بعد (آلاف الطلبات): نعمل بالنتائج فقط
        return _ens(lg, hist, ref, *a, **k) if (lg in have and tier.get(lg, 2) <= 1) else {"running": False, "total": 0, "done": 0, "espn_filled": None}
    def gp_g(lg, hist, use_xg=False):          # ولا نبدأ ضبطًا ثقيلًا في الخلفية: معاملات افتراضية تُعلَّم «غير مضبوطة»
        if lg in have or (lg, use_xg) in service._params: return _gp(lg, hist, use_xg)
        return dict(service.DEFAULT), "tuning"
    xgmod.ensure, service.get_params = ensure_g, gp_g
    logos, mteams, mtier = {}, {}, {}
    from . import review
    archive = _load_arch(); tr = _jl(os.path.join(ARCH, "trackrecord.json"), None) or review.new_tr(); recent, mdate = {}, {}
    try: GEN = requests.get(PREV + "api/gen.json", timeout=20).json()      # متى حُسب كل ملف آخر مرة: ندوّر الأقدم أولًا فتصل كل المباريات دورها
    except Exception: GEN = {}
    if not isinstance(GEN, dict): GEN = {}
    try:
        r = requests.get(PREV + "api/logos.json", timeout=20); logos = r.json() if r.status_code == 200 else {}
    except Exception: logos = {}
    today = datetime.now(timezone.utc).date(); todo, stats = [], {"days": 0, "ok": {}, "fail": {}, "statuses": {}, "skipped": 0, "errors": []}
    for i in range(-1, DAYS):
        d = (today + timedelta(days=i)).isoformat()
        try:
            data = pc.get(f"/v1/date/{d}", {"status": "all"}, ttl=300); w(f"api/matches/{d}.json", data); stats["days"] += 1
        except Exception as e:
            log(f"matches {d}: {e}")
            if not keep_prev(f"api/matches/{d}.json"): continue
            data = json.load(open(os.path.join(OUT, f"api/matches/{d}.json"), encoding="utf-8")); stats["days"] += 1
        try: w(f"api/famous/{d}.json", service.famous_matches(d))
        except Exception as e: log(f"famous {d}: {e}")
        for m in data.get("matches", []):
            for sd in ("home_team", "away_team"):
                t = m.get(sd) or {}
                if t.get("id") and t.get("image_url"): logos[t["id"]] = t["image_url"]
            lid = (m.get("league") or {}).get("id")
            st = str(m.get("status", "")).lower(); stats["statuses"][st] = stats["statuses"].get(st, 0) + 1
            played = m.get("score_home") is not None and m.get("score_away") is not None
            kind = review.phase_of(m)
            if lid in target and m.get("id") and tier[lid] <= 1 and kind in ("live", "post", "pending") and (played or kind in ("live", "pending")): recent[m["id"]] = m; mtier[m["id"]] = tier[lid]
            if i >= 0 and lid in target and not played and st not in DONE and kind == "pre" and m.get("id"):
                todo.append(m["id"]); mtier[m["id"]] = tier[lid]; mdate[m["id"]] = m.get("date"); mteams[m["id"]] = [((m.get("home_team") or {}).get("id"), (m.get("home_team") or {}).get("name")), ((m.get("away_team") or {}).get("id"), (m.get("away_team") or {}).get("name"))]
            elif lid in target: stats["skipped"] += 1
    todo = sorted(dict.fromkeys(todo), key=lambda m: mtier[m]); t2 = [m for m in todo if mtier[m] == 2][:MAX_T2]; todo = ([m for m in todo if mtier[m] < 2] + t2)[:MAX_MATCHES]
    log(f"matches to build: {len(todo)} (tier0={sum(mtier[m] == 0 for m in todo)}, tier1={sum(mtier[m] == 1 for m in todo)}, tier2={len(t2)})")
    stats["tiers"] = {"t0": sum(v == 0 for v in tier.values()), "t1": sum(v == 1 for v in tier.values()), "t2": sum(v == 2 for v in tier.values()), "with_data": len(have)}
    stats["t1_names"] = sorted(l["name"] for l in wl if tier[l["id"]] == 1)[:60]; stats["t2_names_sample"] = sorted(l["name"] for l in wl if tier[l["id"]] == 2)[:40]
    jobs = (("predict", service.predict_match, 2), ("market", service.market_view, 2), ("last10", lambda i: service.last50_match(i, 10), 1),
            ("board", lambda i: wait_board(i, t0 + BUDGET), 1))
    for lvl in (0, 1, 2):                       # الدوريات الكبرى أولًا وبكل أجزائها، ثم المشهورة، ثم البقية (نتائج فقط)
        mids = [m for m in todo if mtier[m] == lvl]
        if lvl: mids.sort(key=lambda m: GEN.get(f"predict/{m}", 0))
        dl = t0 + BUDGET * (0.6 if lvl == 0 else 1.0)
        for mid in mids:
            for name, fn, mx in jobs:
                if lvl > mx: continue
                rel = f"api/{name}/{mid}.json"
                if time.time() > dl:
                    if keep_prev(rel): stats["reused"] = stats.get("reused", 0) + 1
                    continue
                try: w(rel, retry(fn, mid)); GEN[f"{name}/{mid}"] = int(time.time()); stats["ok"][name] = stats["ok"].get(name, 0) + 1
                except Exception as e:
                    stats["fail"][name] = stats["fail"].get(name, 0) + 1; msg = f"{name} {mid}: {type(e).__name__}: {str(e)[:160]}"; log(msg)
                    if len(stats["errors"]) < 40: stats["errors"].append(msg)
                    if keep_prev(rel): stats["reused"] = stats.get("reused", 0) + 1
    av = 0
    for mid in todo:
        try: av += bool(json.load(open(os.path.join(OUT, f"api/market/{mid}.json"), encoding="utf-8")).get("available"))
        except Exception: pass
    stats["market_available"] = av
    try: stats["espn_probe"] = requests.get("https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard", timeout=15, headers={"User-Agent": "Mozilla/5.0 (MatchPredict)"}).status_code
    except Exception as e: stats["espn_probe"] = type(e).__name__
    try: recent_phase(todo, recent, mdate, mtier, archive, tr, stats, log, t0 + BUDGET + 300)
    except Exception as e:
        log(f"phase: {type(e).__name__}: {e}"); stats["errors"].append(f"phase: {type(e).__name__}: {str(e)[:150]}")
    try:    # تشخيص ESPN: لماذا لا تُطابَق المباراة؟
        from . import espn
        dbg = []
        for mid in [m for m in todo if mtier[m] == 0][:3]:
            mm = pc.get(f"/v1/matches/{mid}", ttl=300); lg = next((l for l in leagues if l["id"] == mm["league"]["id"]), None); slug = service._espn_slug(lg) if lg else None
            d = date.fromisoformat(mm["date"]); evs = espn.day_events(slug, d - timedelta(days=1), d + timedelta(days=1)) if slug else []
            names = []
            for e in evs[:4]:
                try: names.append([x["team"].get("displayName") for x in e["competitions"][0]["competitors"]])
                except Exception: pass
            pr = {}
            for k, u in (("range", f"{espn.BASE}/{slug}/scoreboard?dates={(d - timedelta(days=1)):%Y%m%d}-{(d + timedelta(days=1)):%Y%m%d}&limit=300"), ("day", f"{espn.BASE}/{slug}/scoreboard?dates={d:%Y%m%d}"), ("default", f"{espn.BASE}/{slug}/scoreboard")):
                try: rr = requests.get(u, timeout=15, headers={"User-Agent": "Mozilla/5.0 (MatchPredict)"}); pr[k] = [rr.status_code, len((rr.json() or {}).get("events", [])) if rr.status_code == 200 else rr.text[:80]]
                except Exception as ex: pr[k] = type(ex).__name__
            dbg.append({"probe": pr, "slug": slug, "date": mm["date"], "ours": [mm["home_team"]["name"], mm["away_team"]["name"]], "norm": [espn.norm(mm["home_team"]["name"]), espn.norm(mm["away_team"]["name"])], "events": len(evs), "sample": names, "found": bool(slug and espn.find_event(slug, d, mm["home_team"]["name"], mm["away_team"]["name"]))})
        stats["espn_debug"] = dbg
    except Exception as e: stats["espn_debug"] = f"{type(e).__name__}: {str(e)[:120]}"
    ARAB = {"DZ", "MA", "TN", "EG", "SA", "AE", "QA", "JO", "KW", "IQ", "DZA", "MAR", "TUN", "EGY", "SAU", "ARE", "QAT", "JOR", "KWT", "IRQ", "KSA", "UAE", "ALG", "MAR", "TUN", "JOR", "KUW", "OMA", "BHR", "LBA", "SDN"}
    stats["country_codes"] = sorted({str(l.get("country_code")) for l in leagues})[:80]
    stats["arab_leagues"] = [[l["name"], l.get("country_code"), tier.get(l["id"], "مستبعد")] for l in leagues if (l.get("country_code") or "").upper() in ARAB][:40]
    ai_phase([m for m in todo if mtier[m] == 0], stats, log, t0 + BUDGET + 600, mdate)
    extras_phase(todo, mteams, target, wl, tier, have, logos, stats, log, t0 + BUDGET + 1080)
    try:    # اختيارات الرهان بشرط السعر (ESPN) من أفضل أسواق Gemini ومن المحرك
        from . import bets
        items = [bets.for_match(m, _out(f"api/predict/{m}.json"), _out(f"api/ai/{m}.json"), _out(f"api/board/{m}.json"), _out(f"api/market/{m}.json")) for m in todo if mtier.get(m, 2) == 0]
        pk = bets.collect(items); pk["generated_at"] = review.now(); w("api/picks.json", pk); stats["picks"] = {"matches": len(pk["matches"]), "gemini": sum(len(i["gemini"]) for i in pk["matches"]), "engine": sum(len(i["engine"]) for i in pk["matches"]), "high": len(pk["high"])}
    except Exception as e: stats["errors"].append(f"picks: {type(e).__name__}: {str(e)[:150]}")
    built = [m for m in list(todo) + list(recent) if os.path.exists(os.path.join(OUT, f"api/predict/{m}.json"))]; w("api/index.json", {"ids": built})
    ids = set(todo); w("api/gen.json", {k: v for k, v in GEN.items() if k.split("/", 1)[1] in ids})
    # واجهة + مظهر التطبيق
    html = open("static/index.html", encoding="utf-8").read().replace("<script>", "<script>window.STATIC=true;</script>\n<script>", 1)
    html = html.replace("</head>", '<link rel="manifest" href="manifest.webmanifest"><meta name="theme-color" content="#0e1626"><link rel="icon" href="icon-192.png"><link rel="apple-touch-icon" href="icon-192.png"></head>', 1)
    html = html.replace("</body>", '<script>if("serviceWorker"in navigator)navigator.serviceWorker.register("sw.js").catch(()=>{})</script></body>', 1)
    os.makedirs(OUT, exist_ok=True); open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(html)
    for s in (192, 512): open(os.path.join(OUT, f"icon-{s}.png"), "wb").write(png(s))
    w("manifest.webmanifest", {"name": "MatchPredict AI", "short_name": "MatchPredict", "lang": "ar", "dir": "rtl", "start_url": "./", "scope": "./", "display": "standalone",
                               "background_color": "#0e1626", "theme_color": "#0e1626",
                               "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"}, {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]})
    ver = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    open(os.path.join(OUT, "sw.js"), "w", encoding="utf-8").write(
        "const V='mp-%s',S=['./','index.html','manifest.webmanifest','icon-192.png'];\n"
        "self.addEventListener('install',e=>{e.waitUntil(caches.open(V).then(c=>c.addAll(S)));self.skipWaiting()});\n"
        "self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!=V).map(x=>caches.delete(x)))));self.clients.claim()});\n"
        "self.addEventListener('fetch',e=>{const u=new URL(e.request.url);if(u.origin!==location.origin)return;\n"
        " e.respondWith(fetch(e.request).then(r=>{if(r.ok){const c=r.clone();caches.open(V).then(x=>x.put(e.request,c))}return r}).catch(()=>caches.match(e.request)))});\n" % ver)
    w("api/build.json", {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "matches_built": len(todo), "ok": stats["ok"], "fail": stats["fail"], "days": stats["days"], "statuses": stats["statuses"], "skipped_played": stats["skipped"], "errors": stats["errors"], "ai": stats.get("ai"), "ai_models": stats.get("ai_models"), "espn_debug": stats.get("espn_debug"), "arab_leagues": stats.get("arab_leagues"), "country_codes": stats.get("country_codes"), "tiers": stats.get("tiers"), "t1_names": stats.get("t1_names"), "t2_names_sample": stats.get("t2_names_sample"), "extras": stats.get("x"), "phase": stats.get("phase"), "picks": stats.get("picks"), "live_sample": stats.get("live_sample"), "post_sample_keys": stats.get("post_sample_keys"), "market_available": stats.get("market_available"), "espn_probe": stats.get("espn_probe"), "reused": stats.get("reused", 0), "sample_ids": todo[:5]})
    log(json.dumps(stats, ensure_ascii=False)); log(f"done in {int(time.time() - t0)}s")
    return 0 if stats["days"] else 1

if __name__ == "__main__":
    try: sys.exit(main())
    except Exception: traceback.print_exc(); sys.exit(1)
