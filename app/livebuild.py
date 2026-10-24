"""حلقة مباشرة سريعة: كل دقائق تجلب مباريات اليوم، تحسب الحالة الحية للجارية منها (النتيجة، الدقيقة، الاحتمالات المحدَّثة)،
وتنشرها في live.json على فرع live ليقرأه التطبيق مباشرة، دون إعادة بناء الموقع كله. التشغيل: python -m app.livebuild --minutes 45"""
import os, sys, json, time, shutil, subprocess, tempfile
from datetime import datetime, timedelta, timezone
from . import pitch_client as pc, service, review

SNAPS = os.environ.get("ARCH_FILE", "data/archive/snapshots.json")

def once(log=print):
    leagues = pc.get("/v1/leagues", ttl=6 * 3600)["leagues"]
    tier = {l["id"]: service.league_tier(l) for l in leagues if service.wanted_league(l)}
    try: snaps = json.load(open(SNAPS, encoding="utf-8"))
    except Exception: snaps = {}
    today = datetime.now(timezone.utc).date(); out = {}; diag = {"statuses": {}, "sample": None}
    for d in (today - timedelta(days=1), today):
        try: ms = pc.get(f"/v1/date/{d.isoformat()}", {"status": "all"}, ttl=45).get("matches", [])
        except Exception as e: log(f"date {d}: {e}"); continue
        for m in ms:
            lid = (m.get("league") or {}).get("id")
            if lid in tier and tier[lid] <= 1:
                k = str(m.get("status")); diag["statuses"][k] = diag["statuses"].get(k, 0) + 1
                if diag["sample"] is None and review.phase_of(m) == "live": diag["sample"] = {x: m[x] for x in list(m)[:30] if not isinstance(m[x], (dict, list))}
            if lid not in tier or tier[lid] > 1 or not m.get("id") or review.phase_of(m) != "live": continue
            try: evs = pc.get(f"/v1/matches/{m['id']}/events", ttl=30).get("events") or []
            except Exception: evs = []
            st = review.live_state(m, snaps.get(m["id"]), evs)
            out[m["id"]] = {**st, "home": (m.get("home_team") or {}).get("name"), "away": (m.get("away_team") or {}).get("name"), "league": (m.get("league") or {}).get("name"), "status": m.get("status")}
    return {"ts": review.now(), "matches": out, "diag": diag}

def publish(data, log=print):
    """فرع يتيم فيه ملف واحد يُدفع بالقوة (لا يتضخم السجل). يلزم GITHUB_TOKEN و GITHUB_REPOSITORY."""
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo: log("publish: no token/repo (dry run)"); return False
    d = tempfile.mkdtemp(); run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True, text=True)
    try:
        json.dump(data, open(os.path.join(d, "live.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        run("git", "init", "-q"); run("git", "checkout", "-q", "-b", "live"); run("git", "config", "user.name", "data-bot"); run("git", "config", "user.email", "data-bot@users.noreply.github.com")
        run("git", "add", "live.json"); run("git", "commit", "-q", "-m", "live"); run("git", "push", "-q", "-f", f"https://x-access-token:{tok}@github.com/{repo}.git", "live")
        return True
    except subprocess.CalledProcessError as e: log(f"publish failed: {(e.stderr or '')[:200].replace(tok, '***')}"); return False
    finally: shutil.rmtree(d, ignore_errors=True)

def main(argv=None):
    mins = 45
    a = argv or sys.argv[1:]
    if "--minutes" in a: mins = int(a[a.index("--minutes") + 1])
    end = time.time() + mins * 60; last = None; errs = 0
    while time.time() < end:
        try:
            data = once(); sig = json.dumps({k: {x: y for x, y in v.items() if x != "ts"} for k, v in data["matches"].items()}, sort_keys=True) + json.dumps(data["diag"]["statuses"], sort_keys=True)
            if sig != last or not data["matches"] and last is None: publish(data); last = sig
            errs = 0; wait = 150 if data["matches"] else 420
        except Exception as e:
            errs += 1; print(f"live loop: {type(e).__name__}: {str(e)[:150]}"); wait = 120 * errs
            if errs >= 6: return 1
        time.sleep(min(wait, max(1, end - time.time())))
    return 0

if __name__ == "__main__": sys.exit(main())
