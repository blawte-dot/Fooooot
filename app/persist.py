"""بيانات جاهزة محفوظة كملفات gzip صغيرة في مستودع GitHub (مجلد data/): إحصاءات المباريات + درجات الموثوقية + معاملات النموذج.
تُبنى على خوادم GitHub Actions (app/pipeline.py) ويقرؤها التطبيق عند فتحه، فلا حساب ثقيل ولا تحميل مئات الطلبات على Render أو الهاتف."""
import os, gzip, json, time, glob, tempfile, threading
from datetime import datetime, timezone
import requests
from . import xg as xgmod, service

KEYS = ("shots", "sot", "yellow", "corners", "fouls", "xg", "corners_1h", "corners_2h")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED = os.path.join(ROOT, "data")                                                             # يأتي مع الكود
RUNTIME = os.environ.get("DATA_RUNTIME_DIR") or os.path.join(tempfile.gettempdir(), "matchpredict-data")   # يُحدَّث من GitHub وقت التشغيل
_R, _lock = {"last": 0.0, "shas": {}}, threading.Lock()
REFRESH_EVERY = 6 * 3600

def _iso(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

def pack_league(lg, ids, grades, params, espn_filled=None, missing=()):
    idset = set(ids); stats = {}
    for mid in ids:
        d = xgmod.STATS.get(mid)
        if d: stats[mid] = [None if k not in d else round(d[k][i], 2) for k in KEYS for i in (0, 1)]
    return {"v": 1, "league": lg, "generated_at": _iso(), "keys": list(KEYS), "stats": stats, "grades": grades,
            "params": {str(int(u)): {"p": v["p"], "ok": v["ok"]} for u, v in params.items()}, "espn_filled": espn_filled, "missing": sorted(m for m in missing if m in idset)}

def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True); tmp = path + ".tmp"
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=9) as gz:    # mtime=0: نفس المحتوى = نفس البايتات = لا التزام جديد
        gz.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    os.replace(tmp, path)

def _read(path):
    with gzip.open(path, "rt", encoding="utf-8") as f: return json.load(f)

def save_league(doc, out_dir):
    """يكتب الملف فقط إن تغيّر المحتوى فعلًا (غير توقيت التوليد). يرجع True عند الكتابة."""
    path = os.path.join(out_dir, "leagues", doc["league"] + ".json.gz")
    if os.path.exists(path):
        try:
            old = _read(path); same = lambda x: {k: v for k, v in x.items() if k not in ("generated_at", "espn_filled")}
            if same(old) == same(doc): return False
        except Exception: pass
    _write(path, doc); return True

def save_manifest(info, out_dir):
    path = os.path.join(out_dir, "manifest.json"); body = {"v": 1, "leagues": info}
    try:
        if json.load(open(path, encoding="utf-8")).get("leagues") == info: return False
    except Exception: pass
    body["generated_at"] = _iso(); os.makedirs(out_dir, exist_ok=True)
    json.dump(body, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True); return True

def _docs(dirs):
    best = {}
    for d in dirs:
        for p in glob.glob(os.path.join(d, "leagues", "*.json.gz")):
            try: doc = _read(p)
            except Exception: continue
            lg = doc.get("league")
            if doc.get("v") == 1 and lg and (lg not in best or doc.get("generated_at", "") >= best[lg].get("generated_at", "")): best[lg] = doc
    return best

def load(dirs=None):
    """يحمّل كل البيانات الجاهزة إلى الذاكرة: لا انتظار إحصاءات ولا اختبارات موثوقية لهذه الدوريات."""
    docs = _docs(dirs or [BUNDLED, RUNTIME]); now = time.time()
    for doc in docs.values():
        keys = doc["keys"]
        for mid, arr in doc["stats"].items():
            d = {k: (float(arr[2 * i]), float(arr[2 * i + 1])) for i, k in enumerate(keys) if arr[2 * i] is not None and arr[2 * i + 1] is not None}
            if d:
                xgmod.STATS[mid] = d
                if "xg" in d: xgmod.STORE[mid] = d["xg"]
        xgmod.MISSING.update(doc.get("missing") or [])
    for lg, doc in docs.items():
        g = doc.get("grades") or {}
        service._grades[lg] = {"g": g, "state": "done", "until": now + 24 * 3600, "stamp": len(xgmod.STATS), "finished": len(g), "total": len(g), "errors": []}
        for u, v in (doc.get("params") or {}).items(): service._params[(lg, bool(int(u)))] = {"p": v["p"], "until": now + 24 * 3600, "ok": v["ok"]}
        xgmod._state[lg] = {"running": False, "total": 0, "done": 0, "espn_filled": doc.get("espn_filled")}; xgmod._espn_done[lg] = now + 7 * 24 * 3600
    return {"leagues": len(docs), "matches": len(xgmod.STATS)}

# ---- تحديث من GitHub عند أول طلب بعد التشغيل (ليس في الخلفية): يحتاج GITHUB_DATA_TOKEN بصلاحية قراءة المحتوى فقط ----
def should_refresh(): return bool(os.environ.get("GITHUB_DATA_TOKEN")) and time.time() - _R["last"] > REFRESH_EVERY

def _get(url, tok, accept):
    return requests.get(url, headers={"Authorization": f"Bearer {tok}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}, timeout=15)

def refresh_remote(force=False):
    tok = os.environ.get("GITHUB_DATA_TOKEN", "").strip(); repo = os.environ.get("GITHUB_REPO", "blawte-dot/Fooooot"); br = os.environ.get("GITHUB_DATA_BRANCH", "main")
    if not tok: return {"ok": False, "reason": "no_token"}
    with _lock:
        if not force and time.time() - _R["last"] < REFRESH_EVERY: return {"ok": True, "skipped": True}
        _R["last"] = time.time()
    try:
        r = _get(f"https://api.github.com/repos/{repo}/contents/data/leagues?ref={br}", tok, "application/vnd.github+json")
        if r.status_code != 200: return {"ok": False, "reason": f"list_{r.status_code}"}
        got = 0
        for it in r.json():
            name, sha = it.get("name", ""), it.get("sha")
            if not name.endswith(".json.gz") or "/" in name or _R["shas"].get(name) == sha: continue
            fr = _get(it["url"], tok, "application/vnd.github.raw+json")
            if fr.status_code != 200: continue
            try: json.loads(gzip.decompress(fr.content))          # لا نكتب ملفًا تالفًا
            except Exception: continue
            path = os.path.join(RUNTIME, "leagues", name); os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "wb") as f: f.write(fr.content)
            os.replace(path + ".tmp", path); _R["shas"][name] = sha; got += 1
        if got: load()
        return {"ok": True, "files": got}
    except (requests.RequestException, ValueError): return {"ok": False, "reason": "network"}
