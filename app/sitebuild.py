"""يبني موقعًا ثابتًا (تطبيق PWA) من نتائج المحرك الإحصائي: يعمل على GitHub Actions ثم يُنشر على GitHub Pages.
الهاتف يفتح ملفات JSON جاهزة فقط، فلا خادم ولا حساب ثقيل عليه. التشغيل: PITCHAPI_KEY=... python -m app.sitebuild"""
import os, sys, json, time, shutil, zlib, struct, traceback
from datetime import date, datetime, timedelta, timezone
from . import pitch_client as pc, service, persist

OUT = os.environ.get("SITE_DIR", "site")
DAYS = int(os.environ.get("SITE_DAYS", "5"))          # عدد الأيام القادمة المحسوبة مسبقًا
MAX_MATCHES = int(os.environ.get("SITE_MAX", "120"))   # سقف المباريات التفصيلية
BUDGET = int(os.environ.get("SITE_BUDGET_MIN", "45")) * 60
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

def wait_board(mid, deadline):
    while True:
        r = service.board_match(mid)
        busy = r["status"]["stats_loading"] or (r.get("grades_state") or {}).get("state") == "running"
        if not busy or time.time() > deadline: return r
        time.sleep(15)

def main(log=print):
    t0 = time.time(); persist.load()
    leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]
    w("api/leagues.json", {"leagues": [{**l, "big5": service.big5_label(l), "ucl": service.is_ucl(l)} for l in leagues]})
    target = {l["id"] for l in leagues if service.big5_label(l) or service.is_ucl(l)}
    today = datetime.now(timezone.utc).date(); todo, stats = [], {"days": 0, "ok": {}, "fail": {}, "statuses": {}, "skipped": 0, "errors": []}
    for i in range(0, DAYS):
        d = (today + timedelta(days=i)).isoformat()
        try:
            data = pc.get(f"/v1/date/{d}", {"status": "all"}, ttl=300); w(f"api/matches/{d}.json", data); stats["days"] += 1
        except Exception as e: log(f"matches {d}: {e}"); continue
        try: w(f"api/famous/{d}.json", service.famous_matches(d))
        except Exception as e: log(f"famous {d}: {e}")
        for m in data.get("matches", []):
            lid = (m.get("league") or {}).get("id")
            st = str(m.get("status", "")).lower(); stats["statuses"][st] = stats["statuses"].get(st, 0) + 1
            played = m.get("score_home") is not None and m.get("score_away") is not None
            if lid in target and not played and st not in DONE and m.get("id"): todo.append(m["id"])
            elif lid in target: stats["skipped"] += 1
    todo = list(dict.fromkeys(todo))[:MAX_MATCHES]; log(f"matches to build: {len(todo)}")
    jobs = (("predict", service.predict_match), ("last7", lambda i: service.last50_match(i, 7)), ("market", service.market_view),
            ("board", lambda i: wait_board(i, t0 + BUDGET)))
    for name, fn in jobs:
        for mid in todo:
            if time.time() - t0 > BUDGET: log(f"budget reached during {name}"); break
            try: w(f"api/{name}/{mid}.json", fn(mid)); stats["ok"][name] = stats["ok"].get(name, 0) + 1
            except Exception as e:
                stats["fail"][name] = stats["fail"].get(name, 0) + 1; msg = f"{name} {mid}: {type(e).__name__}: {str(e)[:160]}"; log(msg)
                if len(stats["errors"]) < 40: stats["errors"].append(msg)
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
    w("api/build.json", {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "matches_built": len(todo), "ok": stats["ok"], "fail": stats["fail"], "days": stats["days"], "statuses": stats["statuses"], "skipped_played": stats["skipped"], "errors": stats["errors"], "sample_ids": todo[:5]})
    log(json.dumps(stats, ensure_ascii=False)); log(f"done in {int(time.time() - t0)}s")
    return 0 if stats["days"] else 1

if __name__ == "__main__":
    try: sys.exit(main())
    except Exception: traceback.print_exc(); sys.exit(1)
