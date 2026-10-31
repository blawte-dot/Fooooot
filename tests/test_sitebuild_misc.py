import json, os, sys, types
from datetime import date, timedelta, datetime, timezone

def _fake_resp(obj):
    r = types.SimpleNamespace(status_code=200, content=json.dumps(obj).encode()); r.json = lambda: obj; return r

def test_prev_fresh_version_only_when_asked(tmp_path, monkeypatch):
    from app import sitebuild as sb
    monkeypatch.setattr(sb, "OUT", str(tmp_path)); now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    monkeypatch.setattr(sb.requests, "get", lambda *a, **k: _fake_resp({"x": 1, "_gen": now}))
    assert sb.prev_fresh("api/standings/L.json", 2) is True            # ملف بلا _v يُعاد استخدامه (انحدار v4.7)
    monkeypatch.setattr(sb.requests, "get", lambda *a, **k: _fake_resp({"x": 1, "_gen": now, "_v": 1}))
    assert sb.prev_fresh("api/ai/m.json", 2, 3) is False and sb.prev_fresh("api/ai/m.json", 2, 1) is True

def test_profile_phase_writes_matchup_and_ai_context(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.dirname(__file__))
    import test_profile as T
    from app import sitebuild as sb, service, profile as P, ai
    monkeypatch.setattr(sb, "OUT", str(tmp_path)); monkeypatch.setattr(sb, "keep_prev", lambda rel: False)
    monkeypatch.setattr(sb.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    hist = T.make_hist(); ref = date.today() + timedelta(days=1); ctx = P.league_ctx(hist, ref)
    g = lambda t: sorted([h for h in hist if t in (h[1], h[2])], reverse=True)[:50]
    monkeypatch.setattr(service, "team_profile", lambda tid, nm, comps: P.build_profile(tid, nm, g(tid), {}, ctx, ref))
    stats = {"errors": []}; ai.EXTRA.clear()
    sb.profile_phase(["m1"], {"m1": 0}, {"m1": [("A", "Alpha"), ("C", "Charlie")]}, {"m1": "L"}, [], stats, print, 10 ** 12)
    mu = json.load(open(tmp_path / "api/matchup/m1.json", encoding="utf-8"))
    assert stats["profiles"]["new"] == 2 and stats["profiles"]["matchups"] == 1 and mu["notes"] and "home_style" in ai.EXTRA["m1"]
    assert os.path.exists(tmp_path / "api/profile/A.json") and ai.clean({"summary": "s", "style_notes": ["ملاحظة 1.79", "x"], "model_view_ok": True}, {"events": [], "model": {}, "profiles": {"a": 1.79}})["style_notes"]
