import gzip, json, os, random, subprocess, time
from datetime import date, timedelta
import pytest
from app import persist, pipeline, service, xg as xgm, pitch_client as pc, espn, last50

def _reset():
    for d in (xgm.STATS, xgm.STORE, xgm._state, xgm._espn_done, xgm.SLUG, service._grades, service._params): d.clear()
    xgm.MISSING.clear()

@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    _reset(); monkeypatch.setattr(espn, "_get", lambda url, ttl=0: None); yield; _reset(); xgm.BLOCK = False; service.BLOCK_TUNE = False

def fake_league(calls):
    random.seed(9); teams = [f"t_{i:06d}" for i in range(14)]; st = {t: 0.6 + 0.09 * i for i, t in enumerate(teams)}; S, n, seasons = {}, [0], {"2025/2026": [], "2026/2027": []}
    P = lambda l: next(k for k in range(60) if random.random() < 0.0 or True) if False else sum(1 for _ in range(int(l * 6)) if random.random() < 1 / 6)
    for sn, start in (("2025/2026", date.today() - timedelta(days=400)), ("2026/2027", date.today() - timedelta(days=200))):
        i = 0
        for h in teams:
            for a in teams:
                if h == a: continue
                n[0] += 1; mid = f"m_{n[0]:06d}"; d = start + timedelta(days=i); i += 1; pz = st[h] * st[a]
                S[mid] = (P(5 * pz * 1.1), P(5 * pz * .9), P(1.8), P(1.5), P(4.8 * pz), P(4.2 * pz), P(1.6 * pz), P(1.3 * pz))
                seasons[sn].append({"id": mid, "date": d.isoformat(), "time_utc": d.isoformat() + "T15:00:00Z", "status": "finished", "home_team": {"id": h, "name": h}, "away_team": {"id": a, "name": a},
                                    "score_home": P(1.4 * st[h]), "score_away": P(1.1 * st[a])})
    item = lambda t, k, a, b: {"title": t, "key": k, "home": str(a), "away": str(b)}
    L = [{"id": "l_PLXXXX", "name": "Premier League", "country_code": "ENG", "seasons": list(seasons)}, {"id": "l_OTHER0", "name": "Obscure League", "country_code": "XXX", "seasons": ["2026/2027"]}]
    def fake(path, params=None, ttl=0):
        if path == "/v1/leagues": return {"leagues": L}
        if path == "/v1/leagues/l_PLXXXX/matches": return {"matches": seasons[params["season"]]}
        if path.endswith("/stats"):
            calls.append(path); a = S[path.split("/")[3]]
            return {"periods": [{"period": "All", "groups": [{"items": [item("Shots on target", "shots_on_target", a[0], a[1]), item("Yellow cards", "yellow_cards", a[2], a[3]), item("Corners", "corners", a[4] + a[6], a[5] + a[7]),
                                                                          item("Expected goals", "expected_goals", round(a[0] * .3, 2), round(a[1] * .3, 2))]}]},
                                {"period": "1st Half", "groups": [{"items": [item("Corners", "corners", a[4], a[5])]}]}, {"period": "2nd Half", "groups": [{"items": [item("Corners", "corners", a[6], a[7])]}]}]}
        if path.endswith("/events"): return {"events": []}
        raise AssertionError(path)
    return fake

def test_pipeline_builds_files_then_incremental_then_app_loads_instantly(monkeypatch, tmp_path):
    calls = []; monkeypatch.setattr(pc, "get", fake_league(calls))
    r = pipeline.run(out_dir=str(tmp_path), log=lambda *a: None)
    assert r["leagues"] == 1 and r["files_written"] == 1                                      # الخمس الكبرى فقط: الدوري الغامض تُرك
    f = tmp_path / "leagues" / "l_PLXXXX.json.gz"; man = json.load(open(tmp_path / "manifest.json", encoding="utf-8"))
    doc = json.loads(gzip.decompress(f.read_bytes()))
    assert f.stat().st_size < 120_000 and len(doc["stats"]) == 364 and {"goals", "corners", "corners_1h", "sot", "yellow"} <= set(doc["grades"]) and set(doc["params"]) >= {"0"} and man["leagues"]["l_PLXXXX"]["with_stats"] == 364
    n1 = len(calls); assert n1 >= 364
    mt = f.stat().st_mtime_ns; time.sleep(0.02)
    # عملية جديدة (كأنها تشغيل Actions ثانٍ): ابدأ مما هو محفوظ
    _reset(); persist.load([str(tmp_path)]); calls.clear()
    r2 = pipeline.run(out_dir=str(tmp_path), log=lambda *a: None)
    assert calls == [] and r2["files_written"] == 0 and f.stat().st_mtime_ns == mt              # لا طلبات جديدة ولا التزام جديد
    # التطبيق عند فتحه: يقرأ الملف فيجهز كل شيء بلا خيوط ولا طلبات
    _reset(); monkeypatch.setattr(last50, "_match_stats", lambda *a: (_ for _ in ()).throw(AssertionError("لا طلبات إحصاءات"))); monkeypatch.setattr(service.threading, "Thread", lambda *a, **k: (_ for _ in ()).throw(AssertionError("لا خيوط")))
    info = persist.load([str(tmp_path)]); assert info == {"leagues": 1, "matches": 364}
    hist = service.history("l_PLXXXX", ["2025/2026", "2026/2027"], date.today() + timedelta(days=1))
    g = service.get_grades("l_PLXXXX", hist, hist, date.today()); assert g["goals"] is not None and service.grades_state("l_PLXXXX")["state"] == "done"
    p, status = service.get_params("l_PLXXXX", hist, False); assert status == "tuned" and p["xi"] > 0
    st = xgm.ensure("l_PLXXXX", hist, date.today() + timedelta(days=1)); assert st["running"] is False and st["total"] == 0
    assert xgm.STATS[hist[0][5]]["corners_1h"][0] >= 0 and xgm.STORE                                  # ركنيات الشوط والـxG رجعت

def test_save_is_deterministic_and_skips_unchanged(tmp_path):
    xgm.STATS["m1"] = {"shots": (10.0, 5.0), "corners": (6.0, 4.0)}
    d1 = persist.pack_league("l_A", ["m1"], {"goals": {"grade": "جيد", "n": 5}}, {False: {"p": {"xi": 0.002}, "ok": True}})
    assert persist.save_league(d1, str(tmp_path)) is True; f = tmp_path / "leagues" / "l_A.json.gz"; b1 = f.read_bytes()
    d2 = persist.pack_league("l_A", ["m1"], {"goals": {"grade": "جيد", "n": 5}}, {False: {"p": {"xi": 0.002}, "ok": True}}); d2["generated_at"] = "2099-01-01T00:00:00+00:00"
    assert persist.save_league(d2, str(tmp_path)) is False and f.read_bytes() == b1                       # لا تغيير إلا بمحتوى جديد
    xgm.STATS["m2"] = {"shots": (1.0, 2.0)}; d3 = persist.pack_league("l_A", ["m1", "m2"], {}, {}); assert persist.save_league(d3, str(tmp_path)) is True and f.read_bytes() != b1
    assert persist.save_manifest({"l_A": {"x": 1}}, str(tmp_path)) is True and persist.save_manifest({"l_A": {"x": 1}}, str(tmp_path)) is False

class Resp:
    def __init__(self, code, js=None, content=b""): self.status_code, self._j, self.content = code, js, content
    def json(self): return self._j

def test_refresh_from_github_lazy_safe_and_cached(monkeypatch, tmp_path):
    xgm.STATS["m1"] = {"shots": (10.0, 5.0)}; doc = persist.pack_league("l_R", ["m1"], {"goals": {"grade": "جيد", "n": 5}}, {False: {"p": {"xi": 0.002}, "ok": True}})
    persist.save_league(doc, str(tmp_path / "src")); gz = (tmp_path / "src" / "leagues" / "l_R.json.gz").read_bytes(); _reset()
    monkeypatch.setattr(persist, "RUNTIME", str(tmp_path / "rt")); monkeypatch.setattr(persist, "_R", {"last": 0.0, "shas": {}}); seen = []
    def get(url, tok, accept):
        seen.append((url, accept, tok))
        if "contents/data/leagues?" in url: return Resp(200, [{"name": "l_R.json.gz", "sha": "s1", "url": "https://api.github.com/repos/x/y/contents/data/leagues/l_R.json.gz?ref=main"}, {"name": "../evil.json.gz", "sha": "z", "url": "https://x"}, {"name": "bad.json.gz", "sha": "b", "url": "https://api.github.com/bad"}])
        if url.endswith("bad") : return Resp(200, content=b"not gzip")
        return Resp(200, content=gz)
    monkeypatch.setattr(persist, "_get", get)
    assert persist.refresh_remote() == {"ok": False, "reason": "no_token"} and not persist.should_refresh()
    monkeypatch.setenv("GITHUB_DATA_TOKEN", "TOK123"); assert persist.should_refresh()
    r = persist.refresh_remote(); assert r == {"ok": True, "files": 1} and "m1" in xgm.STATS and service._grades["l_R"]["g"]["goals"]["grade"] == "جيد"    # الملف التالف واسم المسار الملغوم تُجُوهلا
    assert (tmp_path / "rt" / "leagues" / "l_R.json.gz").exists() and not (tmp_path / "evil.json.gz").exists() and "TOK123" not in json.dumps(r) and not persist.should_refresh()
    n = len(seen); assert persist.refresh_remote()["skipped"] is True and len(seen) == n                    # خلال 6 ساعات لا طلبات
    r = persist.refresh_remote(force=True); assert r["files"] == 0 and sum("l_R.json.gz" in u and "ref=main" in u and "leagues?" not in u for u, a, t in seen) == 1   # نفس sha: لا تنزيل ثانٍ
    monkeypatch.setattr(persist, "_get", lambda *a: Resp(404)); assert persist.refresh_remote(force=True) == {"ok": False, "reason": "list_404"}
    monkeypatch.setattr(persist, "_get", lambda *a: (_ for _ in ()).throw(persist.requests.ConnectionError("x"))); assert persist.refresh_remote(force=True)["reason"] == "network"   # الشبكة لا تكسر التطبيق

def test_endpoint_hook_and_files_are_valid(monkeypatch):
    from app.main import app
    called = []; monkeypatch.setattr(persist, "should_refresh", lambda: True); monkeypatch.setattr(persist, "refresh_remote", lambda force=False: called.append(1))
    c = app.test_client(); c.get("/api/health"); c.get("/"); assert called == [1]                      # عند أول طلب API فقط، لا عند الصفحة الرئيسية
    r = c.get("/api/data-source").json; assert "leagues" in r and "remote_refresh" in r
    import yaml
    wf = yaml.safe_load(open(".github/workflows/refresh-data.yml", encoding="utf-8")); on = wf.get("on") or wf.get(True)
    assert "schedule" in on and "workflow_dispatch" in on and wf["permissions"]["contents"] == "write"
    steps = wf["jobs"]["refresh"]["steps"]; assert any("app.pipeline" in (s.get("run") or "") and "PITCHAPI_KEY" in str(s.get("env")) for s in steps) and any("[skip render]" in (s.get("run") or "") for s in steps)
    assert subprocess.run(["sh", "-n", "start.sh"]).returncode == 0 and os.access("start.sh", os.X_OK)
