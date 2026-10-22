"""يعمل على خوادم GitHub Actions (لا على Render ولا على الهاتف): يجلب الإحصاءات الناقصة فقط، يختبر الأسواق، ويحفظ النتائج في data/.
التشغيل: PITCHAPI_KEY=... python -m app.pipeline [league_id ...]"""
import os, sys, time, traceback
from datetime import date, timedelta
from . import pitch_client as pc, service, xg as xgmod, persist

def targets(leagues, only=None):
    if only: return [l for l in leagues if l["id"] in only]
    return [l for l in leagues if service.big5_label(l) or service.is_ucl(l)]      # الخمس الكبرى + دوري أبطال أوروبا

def run(only=None, out_dir=None, log=print):
    out_dir = out_dir or persist.BUNDLED
    xgmod.BLOCK = True; service.BLOCK_TUNE = True          # على خادم قوي ننتظر الاكتمال داخل العملية
    leagues = pc.get("/v1/leagues", ttl=0)["leagues"]; ref = date.today() + timedelta(days=1); info, changed = {}, 0
    for l in targets(leagues, only):
        lg, t0 = l["id"], time.time()
        try:
            hist = service.history(lg, service._all_seasons(leagues, lg), ref)
            if len(hist) < 100: log(f"تخطي {l['name']}: {len(hist)} مباراة فقط"); continue
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
    persist.save_manifest(info, out_dir)
    return {"leagues": len(info), "files_written": changed}

if __name__ == "__main__":
    if not os.environ.get("PITCHAPI_KEY"): print("PITCHAPI_KEY غير مضبوط"); sys.exit(2)
    persist.load()                                         # ابدأ مما هو محفوظ: يجلب الجديد فقط
    print(run(only=sys.argv[1:] or None))
