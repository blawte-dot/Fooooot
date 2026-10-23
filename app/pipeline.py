"""يعمل على خوادم GitHub Actions (لا على Render ولا على الهاتف): يجلب الإحصاءات الناقصة فقط، يختبر الأسواق، ويحفظ النتائج في data/.
التشغيل: PITCHAPI_KEY=... python -m app.pipeline [league_id ...]"""
import os, sys, time, json, traceback
from datetime import date, timedelta, datetime, timezone
from . import pitch_client as pc, service, xg as xgmod, persist

def targets(leagues, only=None):
    if only: return [l for l in leagues if l["id"] in only]
    return sorted([l for l in leagues if service.wanted_league(l)], key=lambda l: (service.league_tier(l), l["name"]))   # المستوى 0 ثم 1 ثم 2

def _fresh(lg, hours):
    try:
        d = persist._docs([persist.BUNDLED]).get(lg)
        return bool(d) and (datetime.now(timezone.utc) - datetime.fromisoformat(d["generated_at"])).total_seconds() < hours * 3600
    except Exception: return False

def run(only=None, out_dir=None, log=print):
    out_dir = out_dir or persist.BUNDLED
    budget = float(os.environ.get("PIPELINE_BUDGET_MIN", "35")) * 60; T0 = time.time()
    xgmod.BLOCK = True; service.BLOCK_TUNE = True          # على خادم قوي ننتظر الاكتمال داخل العملية
    leagues = pc.get("/v1/leagues", ttl=0)["leagues"]; ref = date.today() + timedelta(days=1); info, changed = {}, 0
    for l in targets(leagues, only):
        lg, t0 = l["id"], time.time(); tier = service.league_tier(l)
        if not only and tier > 0 and (time.time() - T0 > budget or _fresh(lg, 24 if tier == 1 else 72)): continue    # الدوريات الأخرى تتقدم تدريجيًا بين التشغيلات
        try:
            hist = service.history(lg, service._all_seasons(leagues, lg), ref)
            if len(hist) < 100: log(f"تخطي {l['name']}: {len(hist)} مباراة فقط"); continue
            if tier == 2:           # نتائج فقط: نضبط المعاملات بلا جلب إحصاءات (آلاف الطلبات لدوريات صغيرة)
                service._params.pop((lg, False), None); service.get_params(lg, hist, False)
                p0 = service._params.get((lg, False)); doc = persist.pack_league(lg, [], {}, {False: p0} if p0 else {}, None, set())
                w = persist.save_league(doc, out_dir); changed += w; info[lg] = {"name": l["name"], "matches": len(hist), "with_stats": 0, "errors": []}
                log(f"{'كُتب' if w else 'بلا تغيير'} (نتائج فقط): {l['name']} — {len(hist)} مباراة، {time.time() - t0:.0f} ث"); continue
            slug = service._espn_slug(l)
            if slug: xgmod.SLUG[lg] = slug
            for h in hist:                                  # أعد محاولة المباريات الحديثة التي لم تكن إحصاءاتها جاهزة
                if (ref - h[0]).days <= 14: xgmod.MISSING.discard(h[5])
            xgmod._espn_done.pop(lg, None)
            st = xgmod.ensure(lg, hist, ref)
            for k in [lg, (lg, False), (lg, True)]: service._grades.pop(k, None); service._params.pop(k, None)
            usable = xgmod.coverage(hist, ref) >= 0.6
            service.get_grades(lg, hist, hist, ref)
            params = {}
            for u in {False, usable}:
                service.get_params(lg, hist, u)
                if (lg, u) in service._params: params[u] = service._params[(lg, u)]
            doc = persist.pack_league(lg, [h[5] for h in hist], service._grades[lg]["g"], params, st.get("espn_filled"), xgmod.MISSING)
            w = persist.save_league(doc, out_dir); changed += w
            info[lg] = {"name": l["name"], "matches": len(hist), "with_stats": len(doc["stats"]), "errors": service._grades[lg]["errors"][:3]}
            log(f"{'كُتب' if w else 'بلا تغيير'}: {l['name']} — {len(hist)} مباراة، إحصاءات {len(doc['stats'])}، {time.time() - t0:.0f} ث")
        except Exception as e:
            traceback.print_exc(); log(f"فشل {l['name']}: {type(e).__name__}")
    try: old = json.load(open(os.path.join(out_dir, "manifest.json"), encoding="utf-8")).get("leagues", {})
    except Exception: old = {}
    persist.save_manifest({**old, **info}, out_dir)
    return {"leagues": len(info), "files_written": changed}

if __name__ == "__main__":
    if not os.environ.get("PITCHAPI_KEY"): print("PITCHAPI_KEY غير مضبوط"); sys.exit(2)
    persist.load()                                         # ابدأ مما هو محفوظ: يجلب الجديد فقط
    print(run(only=sys.argv[1:] or None))
