from app import review as R

def test_inplay_start_matches_poisson_and_end_is_decided():
    a = R.inplay(0, 0, 0, 1.6, 1.1); assert abs(sum(a["p"]) - 100) < 0.3 and a["p"][0] > a["p"][2]
    z = R.inplay(2, 0, 90, 1.6, 1.1); assert z["p"][0] > 99.0
    t = R.inplay(1, 1, 80, 1.5, 1.5); assert 60 < t["p"][1] + 0 < 100 or t["p"][1] > 40
    assert all(l["line"] >= 1.5 for l in R.inplay(1, 1, 50, 1.4, 1.2)["lines"]) and R.inplay(1, 1, 50, 1.4, 1.2)["btts"] > 50

def test_red_card_shifts_probability():
    base = R.inplay(0, 0, 30, 1.5, 1.5)["p"]; red = R.inplay(0, 0, 30, 1.5, 1.5, rh=1)["p"]
    assert red[0] < base[0] and red[2] > base[2]

def test_evaluate_labels():
    act = {"goals": (2, 1), "corners": (6, 4)}
    ev = lambda g, l: R.evaluate(g, l, "Chelsea", "Spurs", act)
    assert ev("result", "فوز Chelsea") is True and ev("result", "تعادل") is False and ev("result", "Spurs أو تعادل") is False
    assert ev("goals", "المجموع أكثر من 2.5") is True and ev("goals", "المجموع أقل من 2.5") is False
    assert ev("goals", "كلا الفريقين يسجل: نعم") is True and ev("goals", "Spurs شباكه نظيفة") is False
    assert ev("goals", "Chelsea يسجل أكثر من 1.5") is True and ev("corners", "Spurs أكثر من 4.5") is False
    assert ev("corners", "Chelsea الأكثر عددًا") is True and ev("yellow", "المجموع أكثر من 3.5") is None

def test_review_and_trackrecord():
    snap = {"hn": "Chelsea", "an": "Spurs", "p": [55.0, 25.0, 20.0], "xg": [1.7, 1.0], "ev": [["result", "فوز Chelsea", 55.0, "نعم", "جيد"], ["goals", "المجموع أكثر من 2.5", 64.0, "نعم", "مقبول"]],
            "exp": {"goals": {"label": "الأهداف", "home": 1.7, "away": 1.0, "total": 2.7}}}
    rev = R.review(snap, {"score_home": 2, "score_away": 1})
    assert rev["result"] == "H" and rev["top_hit"] and rev["compare"][0]["act"]["total"] == 3 and all(p["ok"] for p in rev["picks"])
    tr = R.new_tr(); assert R.update_tr(tr, "m1", rev, snap) and not R.update_tr(tr, "m1", rev, snap)
    v = R.tr_view(tr); assert v["matches"] == 1 and v["top_hit_pct"] == 100.0
    assert not R.update_tr(R.new_tr(), "m2", rev, {**snap, "retro": True})

def test_phase_and_minute():
    assert R.phase_of({"status": "finished", "score_home": 1, "score_away": 0}) == "post" and R.phase_of({"status": "not_started"}) == "pre"
    assert R.phase_of({"status": "inprogress", "score_home": 0, "score_away": 0}) == "live"
    assert R.minute_of({"minute": "63'"})[0] == 63 and R.minute_of({}, [{"minute": 38}, {"minute": 12}]) == (38, "آخر حدث (حد أدنى)")

def test_time_based_live_detection_and_events_score():
    from datetime import datetime, timedelta, timezone
    iso = lambda mins: (datetime.now(timezone.utc) - timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert R.phase_of({"status": "not_started", "time_utc": iso(30)}) == "live" and R.phase_of({"status": "not_started", "time_utc": iso(200)}) == "pending" and R.phase_of({"status": "not_started", "time_utc": iso(400)}) == "stale"
    assert R.phase_of({"status": "not_started", "time_utc": iso(-120)}) == "pre" and R.phase_of({"status": "finished", "time_utc": iso(300)}) == "post"
    m = {"status": "not_started", "time_utc": iso(75), "home_team": {"id": "h"}, "away_team": {"id": "a"}}
    evs = [{"event_type": "goal", "team_id": "h", "minute": 10}, {"event_type": "own_goal", "team_id": "h", "minute": 30}, {"event_type": "yellow_card", "team_id": "a", "minute": 33}]
    assert R.goals_from_events(m, evs) == (1, 1)
    st = R.live_state(m, {"p": [50, 25, 25], "xg": [1.5, 1.0]}, evs); assert st["score"] == [1, 1] and 55 <= st["minute"] <= 62 and st["inplay"]["p"][1] > 20
    assert R.live_state(m, None, [])["score"] is None

def test_not_used_reflects_actual_usage():
    from app import service
    assert service.not_used(0.15, 0.3) == ["الإصابات والغيابات والتشكيلة"]
    assert service.not_used(0, 0) == ["Elo", "xG التاريخي", "الإصابات والغيابات والتشكيلة"] and service.not_used(0.2, 0) == ["xG التاريخي", "الإصابات والغيابات والتشكيلة"]
