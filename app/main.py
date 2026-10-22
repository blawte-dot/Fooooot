import os, re, gzip
from flask import Flask, jsonify, request, send_from_directory
from . import pitch_client as pc, service, ai, persist

app = Flask(__name__, static_folder="../static", static_url_path="")
try: persist.load()          # بيانات جاهزة من مجلد data/ (تُبنى على GitHub Actions)
except Exception: pass

@app.errorhandler(pc.PitchError)
def _pe(e): return jsonify({"error": {"code": e.code, "message": str(e)}}), e.status

@app.get("/")
def index(): return send_from_directory(app.static_folder, "index.html")

@app.get("/api/health")
def health(): return {"ok": True, "key_configured": bool(os.environ.get("PITCHAPI_KEY"))}

@app.get("/api/leagues")
def leagues(): return {"leagues": [{**l, "big5": service.big5_label(l), "ucl": service.is_ucl(l)} for l in pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]]}

@app.get("/api/matches")
def matches():
    d = request.args.get("date", "")
    if not service.DATE_RE.match(d): return {"error": {"code": "INVALID_PARAMETER", "message": "التاريخ يجب أن يكون YYYY-MM-DD"}}, 400
    return pc.get(f"/v1/date/{d}", {"status": "all"}, ttl=300)

@app.get("/api/predict/<match_id>")
def predict(match_id):
    if not service.ID_RE.match(match_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return service.predict_match(match_id)

@app.get("/api/backtest/<league_id>")
def backtest(league_id):
    if not service.ID_RE.match(league_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return service.backtest_league(league_id)

@app.get("/api/league/<league_id>/upcoming")
def upcoming(league_id):
    if not service.ID_RE.match(league_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    d = pc.get(f"/v1/leagues/{league_id}/matches", {"status": "upcoming"}, ttl=600)
    return {"league": d.get("league"), "matches": d.get("matches", [])}

@app.get("/api/last50/<match_id>")
def last50(match_id):
    if not service.ID_RE.match(match_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return service.last50_match(match_id)

@app.get("/api/teams")
def teams(): return service.search_teams(request.args.get("q", ""), request.args.get("big5") == "1")

@app.get("/api/logos")
def logos(): return service.logos(request.args.get("ids", "").split(","))

@app.get("/api/team50/<team_id>")
def team50(team_id):
    if not service.TEAM_RE.match(team_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    comps = [c for c in request.args.get("comps", "").split(",") if service.ID_RE.match(c) and c.startswith("l_")][:8]
    return service.team50(team_id, "all" if request.args.get("scope") == "all" else "main", comps or None, request.args.get("name", "")[:60])

@app.get("/api/famous")
def famous():
    d = request.args.get("date", "")
    if not service.DATE_RE.match(d): return {"error": {"code": "INVALID_PARAMETER", "message": "التاريخ يجب أن يكون YYYY-MM-DD"}}, 400
    return service.famous_matches(d)

@app.get("/api/board/<match_id>")
def board(match_id):
    if not service.ID_RE.match(match_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return service.board_match(match_id)

@app.get("/api/standings/<league_id>")
def standings(league_id):
    if not service.ID_RE.match(league_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return service.standings(league_id, request.args.get("season") or None)


@app.after_request
def _compress(resp):
    """يضغط ردود JSON/HTML الكبيرة (يوفر بيانات الهاتف ويسرّع التحميل). أي خلل يترك الرد كما هو."""
    try:
        ct = resp.headers.get("Content-Type", "")
        if resp.status_code != 200 or resp.headers.get("Content-Encoding") or "gzip" not in request.headers.get("Accept-Encoding", "").lower(): return resp
        if not any(t in ct for t in ("json", "html", "javascript")): return resp
        resp.direct_passthrough = False; data = resp.get_data()
        if len(data) >= 1024:
            resp.set_data(gzip.compress(data, 5)); resp.headers["Content-Encoding"] = "gzip"; resp.headers["Vary"] = "Accept-Encoding"
        if "html" in ct: resp.headers["Cache-Control"] = "no-cache"   # المتصفح يتحقق من النسخة الجديدة دائمًا
    except Exception: pass
    return resp


def _ip():
    xf = request.headers.get("X-Forwarded-For", "")
    return (xf.split(",")[-1].strip() if xf else request.remote_addr) or "?"   # آخر عنوان يضيفه وسيط الاستضافة (الأقل قابلية للتزوير)

@app.errorhandler(ai.AIError)
def _aie(e): return jsonify({"error": {"code": e.code, "message": str(e)}}), e.status

@app.get("/api/ai/<match_id>")
def ai_analysis(match_id):
    if not service.ID_RE.match(match_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return ai.analyze(match_id, _ip())

@app.get("/api/ai-news/<match_id>")
def ai_news(match_id):
    if not service.ID_RE.match(match_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return ai.news(match_id, _ip())

@app.get("/api/market/<match_id>")
def market(match_id):
    if not service.ID_RE.match(match_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return service.market_view(match_id)

@app.get("/api/ai-dossier/<match_id>")
def ai_dossier(match_id):
    if not service.ID_RE.match(match_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return ai.dossier(match_id, _ip())

@app.get("/api/data-status/<league_id>")
def data_status(league_id):
    if not service.ID_RE.match(league_id): return {"error": {"code": "INVALID_PARAMETER", "message": "معرّف غير صالح"}}, 400
    return service.data_status(league_id)


@app.get("/api/diag")
def diag():
    """تشخيص بلا أسرار: سرعة الخادم الفعلية، حالة الاختبارات والتحميل. افتحه من المتصفح وأرسل لي ما يظهر عند أي بطء."""
    import time as _t, sys
    from datetime import date as _d, timedelta as _td
    from . import model as _m, xg as _xg
    ts = [f"t{i}" for i in range(20)]; rows = []; i = 0
    for _ in range(2):
        for h in ts:
            for a in ts:
                if h != a: rows.append((_d(2024, 8, 1) + _td(days=i // 2), h, a, (i * 7) % 4, (i * 3) % 3, f"m{i}")); i += 1
    t = _t.time(); _m.fit(rows, _d(2026, 10, 1)); ms = round((_t.time() - t) * 1000)
    return {"version": "1.8", "python": sys.version.split()[0], "fit_760_rows_ms": ms, "server_speed": "سريع" if ms < 150 else "متوسط" if ms < 600 else "بطيء (الخطة المجانية ضعيفة المعالج)",
            "grades": {k: {"state": v["state"], "finished": v["finished"], "total": v["total"], "errors": v["errors"][:5], "markets": sorted(v["g"])} for k, v in service._grades.items()},
            "stats_loading": {k: {kk: vv for kk, vv in v.items() if kk in ("running", "total", "done", "espn_filled")} for k, v in _xg._state.items()},
            "stats_loaded": len(_xg.STATS), "stats_missing": len(_xg.MISSING), "cache": {"predict": len(service._PM.d), "board": len(service._BM.d)}}


@app.before_request
def _lazy_refresh():
    """عند فتح التطبيق (أول طلب API بعد التشغيل ثم كل 6 ساعات) يجلب أحدث بيانات جاهزة من GitHub إن ضُبط GITHUB_DATA_TOKEN. ليس خيطًا خلفيًا."""
    try:
        if request.path.startswith("/api/") and persist.should_refresh(): persist.refresh_remote()
    except Exception: pass

@app.get("/api/data-source")
def data_source():
    import os
    from . import xg as _xg
    docs = persist._docs([persist.BUNDLED, persist.RUNTIME])
    return {"leagues": {k: {"generated_at": v.get("generated_at"), "matches_with_stats": len(v.get("stats", {}))} for k, v in docs.items()}, "stats_in_memory": len(_xg.STATS),
            "remote_refresh": "مفعّل" if os.environ.get("GITHUB_DATA_TOKEN") else "غير مفعّل (يعتمد على ملفات data/ المرفقة مع الكود)"}
