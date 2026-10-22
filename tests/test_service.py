import math, random
from datetime import date, timedelta
from app import service, pitch_client as pc, model

def _pois(l):
    L, k, p = math.exp(-l), 0, 1.0
    while True:
        p *= random.random()
        if p <= L: return k
        k += 1

def test_markets_consistency():
    m = model.score_matrix(1.6, 1.1, -0.05); mk = model.markets(m); s = model.summarize(m)
    assert abs(mk["double_chance"]["1X"] - (s["home"] + s["draw"])) < 1e-9
    assert abs(mk["ou"]["2.5"]["over"] + mk["ou"]["2.5"]["under"] - 1) < 1e-9
    ah = mk["ah"]["home_m1"]; assert abs(ah["win"] + ah["push"] + ah["lose"] - 1) < 1e-9
    assert abs(model.poisson_over(3.0, 2.5) - (1 - sum(math.exp(-3) * 3**k / math.factorial(k) for k in range(3)))) < 1e-9

def test_full_pipeline_with_fake_api(monkeypatch):
    random.seed(5)
    teams = [f"t_{i:06d}" for i in range(16)]; st = {t: random.uniform(.6, 1.8) for t in teams}
    seasons, EV, n = {"2024/2025": [], "2025/2026": []}, {}, 0
    for sn, start in (("2024/2025", date(2024, 8, 10)), ("2025/2026", date(2025, 8, 9))):
        i = 0
        for h in teams:
            for a in teams:
                if h == a: continue
                n += 1; mid = f"m_{n:06d}"; d = start + timedelta(days=int(i * 1.25)); i += 1
                seasons[sn].append({"id": mid, "date": d.isoformat(), "time_utc": d.isoformat() + "T15:00:00Z", "status": "finished",
                    "home_team": {"id": h}, "away_team": {"id": a},
                    "score_home": _pois(1.3 * st[h] / st[a] ** .5), "score_away": _pois(st[a] / st[h] ** .5)})
                EV[mid] = [{"event_type": "Goal", "team_id": h}] + [{"event_type": "YellowCard", "team_id": t}
                    for t in (h, a) for _ in range(_pois(1.8))]
    def fake(path, params=None, ttl=0):
        if path == "/v1/leagues": return {"leagues": [{"id": "l_LEAGUE", "name": "Test", "country_code": "TST", "seasons": list(seasons)}]}
        if path == "/v1/leagues/l_LEAGUE/matches": return {"matches": seasons[params["season"]]}
        if path == "/v1/matches/m_TARGET": return {"id": "m_TARGET", "league": {"id": "l_LEAGUE", "name": "Test"}, "home_team": {"id": teams[0], "name": "A"},
                    "away_team": {"id": teams[1], "name": "B"}, "date": "2026-10-20", "time_utc": "2026-10-20T15:00:00Z", "referee": "R"}
        if path.endswith("/events"): return {"events": EV[path.split("/")[3]]}
        raise AssertionError(path)
    monkeypatch.setattr(pc, "get", fake); monkeypatch.setattr(service, "BLOCK_TUNE", True)
    r = service.predict_match("m_TARGET"); p = r["prediction"]
    assert p and p["home"] + p["draw"] + p["away"] == 100
    assert r["params"]["status"] == "tuned"
    assert abs(p["markets"]["ou"]["2.5"]["over"] + p["markets"]["ou"]["2.5"]["under"] - 100) < 0.2
    assert r["cards"] and r["cards"]["home"]["n"] >= 5 and "4.5" in r["cards"]["lines"]

def test_upcoming_endpoint(monkeypatch):
    from app.main import app
    monkeypatch.setattr(pc, "get", lambda path, params=None, ttl=0: {"league": {"id": "l_LEAGUE", "name": "T"}, "matches": [{"id": "m_000001"}]} if params == {"status": "upcoming"} else (_ for _ in ()).throw(AssertionError(path)))
    c = app.test_client()
    assert c.get("/api/league/l_LEAGUE/upcoming").json["matches"][0]["id"] == "m_000001"
    assert c.get("/api/league/bad/upcoming").status_code == 400

def test_extends_history_for_promoted_team(monkeypatch):
    random.seed(11); n = [0]
    def gen(teams, start, lid):
        out = []; i = 0
        for h in teams:
            for a in teams:
                if h == a: continue
                n[0] += 1; d = start + timedelta(days=int(i * 1.2)); i += 1
                out.append({"id": f"m_{n[0]:06d}", "date": d.isoformat(), "time_utc": d.isoformat() + "T15:00:00Z", "status": "finished",
                            "home_team": {"id": h}, "away_team": {"id": a}, "score_home": _pois(1.4), "score_away": _pois(1.1)})
        return out
    A = [f"t_{i:06d}" for i in range(16)]; B = [f"t_B{i:05d}" for i in range(15)] + [A[5]]  # A[5] كان في الدرجة الثانية سابقًا
    data = {"l_AAAAAA": {"2025/2026": gen(A, date(2025, 8, 9), "A")}, "l_BBBBBB": {"2024/2025": gen(B, date(2024, 8, 9), "B")}}
    # فريق جديد في A لم يلعب في A مطلقًا لكنه لعب في B
    newt = B[0]; A2 = A[:15] + [newt]; data["l_AAAAAA"]["2025/2026"] = [x for x in data["l_AAAAAA"]["2025/2026"]
        if A[15] not in (x["home_team"]["id"], x["away_team"]["id"])]
    leagues = [{"id": "l_AAAAAA", "name": "A", "country_code": "XXX", "seasons": ["2025/2026"]},
               {"id": "l_BBBBBB", "name": "B", "country_code": "XXX", "seasons": ["2024/2025"]}]
    def fake(path, params=None, ttl=0):
        if path == "/v1/leagues": return {"leagues": leagues}
        if path.startswith("/v1/leagues/") and path.endswith("/matches"): return {"matches": data[path.split("/")[3]][params["season"]]}
        if path == "/v1/matches/m_TARGET": return {"id": "m_TARGET", "league": {"id": "l_AAAAAA", "name": "A"}, "home_team": {"id": A[0], "name": "H"},
                "away_team": {"id": newt, "name": "N"}, "date": "2026-10-20", "time_utc": "2026-10-20T15:00:00Z"}
        if path.endswith("/events"): return {"events": []}
        raise AssertionError(path)
    monkeypatch.setattr(pc, "get", fake); monkeypatch.setattr(service, "BLOCK_TUNE", True)
    r = service.predict_match("m_TARGET")
    assert r["prediction"] is not None and r["scope"]["extra_matches"] > 0 and r["scope"]["away_n"] >= 8

def test_engine_pieces():
    from app import elo, stats
    d0, rows = date(2025, 1, 1), []
    random.seed(2)
    ts = [f"t{i}" for i in range(10)]; st = {t: 0.5 + i * 0.2 for i, t in enumerate(ts)}; i = 0
    for _ in range(6):
        for h in ts:
            for a in ts:
                if h != a: rows.append((d0 + timedelta(days=i // 2), h, a, _pois(1.3 * st[h] / st[a] ** .5), _pois(st[a] / st[h] ** .5), f"m{i}")); i += 1
    ref = date(2027, 1, 1); em = elo.fit(rows, ref)
    assert em["R"]["t9"] > em["R"]["t0"] and abs(sum(elo.probs(em, "t9", "t0")) - 1) < 1e-9
    assert abs(sum(elo.blend((.5, .3, .2), (.4, .3, .3), .7, 1.1)) - 1) < 1e-9
    mat = model.reweight(model.score_matrix(1.5, 1.1, -.05), (.5, .3, .2)); s = model.summarize(mat)
    assert abs(s["home"] - .5) < 1e-9 and abs(s["draw"] - .3) < 1e-9 and abs(sum(map(sum, mat)) - 1) < 1e-9
    f = stats.form(rows, "t9", ref); assert f["n"] == 10 and len(f["form"]) == 5
    assert stats.h2h(rows, "t9", "t0", ref)["n"] == 6
    from app import backtest
    t = backtest.tune(rows, test_days=200, min_train=150)
    assert t and t["w"] in (1.0, .85, .7, .55) and t["log_loss"] <= t["dc_log_loss"]

def test_last50_counts(monkeypatch):
    random.seed(4); n = [0]; T = "t_TEAM01"; others = [f"t_O{i:05d}" for i in range(13)]
    def gen(teams, start, step):
        out = []; i = 0
        for h in teams:
            for a in teams:
                if h == a: continue
                n[0] += 1; d = start + timedelta(days=int(i * step)); i += 1
                out.append({"id": f"m_{n[0]:06d}", "date": d.isoformat(), "time_utc": d.isoformat() + "T15:00:00Z", "status": "finished",
                            "home_team": {"id": h}, "away_team": {"id": a}, "score_home": 2, "score_away": 1})
        return out
    lea = {"2024/2025": gen([T] + others, date(2024, 8, 10), 1), "2025/2026": gen([T] + others, date(2025, 8, 9), 1)}
    ucl = {"2025/2026": gen([T] + others[:3], date(2025, 9, 15), 3)}
    L = [{"id": "l_LEAGUE", "name": "Test League", "country_code": "TST", "seasons": list(lea)},
         {"id": "l_CHAMPS", "name": "UEFA Champions League", "country_code": "INT", "seasons": list(ucl)}]
    stats_payload = {"periods": [{"period": "All", "groups": [{"group_name": "Top stats", "items": [
        {"title": "Corners", "key": "corners", "home": "5", "away": "4", "format_type": "integer"},
        {"title": "Total shots", "key": "total_shots", "home": "12 (50%)", "away": "8", "format_type": "integerWithPercentage"}]}]}]}
    def fake(path, params=None, ttl=0):
        if path == "/v1/leagues": return {"leagues": L}
        if path == "/v1/leagues/l_LEAGUE/matches": return {"matches": lea[params["season"]]}
        if path == "/v1/leagues/l_CHAMPS/matches": return {"matches": ucl[params["season"]]}
        if path == "/v1/matches/m_TARGET": return {"id": "m_TARGET", "league": {"id": "l_LEAGUE", "name": "Test League"},
            "home_team": {"id": T, "name": "T"}, "away_team": {"id": others[0], "name": "O"}, "date": "2026-10-20", "time_utc": "2026-10-20T15:00:00Z"}
        if path.endswith("/stats"): return stats_payload
        if path.endswith("/events"): return {"events": []}
        raise AssertionError(path)
    monkeypatch.setattr(pc, "get", fake); monkeypatch.setattr(service, "get_params", lambda lg, h, u=False: (dict(service.DEFAULT), "default"))
    r = service.last50_match("m_TARGET"); h = r["home"]
    assert r["outlook"] and r["outlook"]["home"] + r["outlook"]["draw"] + r["outlook"]["away"] == 100
    assert len(h["rows"]) == 50 and h["rows"][0]["corners"] in ([5, 4], [4, 5]) and "expected_home" in r["combined"]["corners"]
    assert h["n"] == 50 and sum(h["competitions"].values()) == 50 and h["competitions"]["champions_league"] > 0
    c = h["metrics"]["corners"]
    assert c["n"] == 50 and c["total_freq"] == [{"value": 9, "matches": 50}] and c["lines"][1] == {"line": 8.5, "over": 50, "under": 0}
    assert r["competitions_used"] == ["Test League", "UEFA Champions League"]
    assert "yellow" not in h["metrics"] and h["metrics"]["shots"]["total_avg"] == 20.0
    assert h["results"]["W"] + h["results"]["D"] + h["results"]["L"] == 50
    vr = h["venue_results"]; assert sum(vr["home"].values()) + sum(vr["away"].values()) == 50
    cr = h["comp_results"]; assert sum(cr["league"].values()) + sum(cr["champions_league"].values()) == 50
    assert h["points"] == 3 * h["results"]["W"] + h["results"]["D"] and h["gf"] > 0
    assert r["combined"]["corners"]["lines"][1]["avg_pct"] == 100 and any("corners" in k for k in r["available_stat_keys"])

def test_team_search_and_team50(monkeypatch):
    n = [0]; T = "t_BARCEL"; others = [f"t_O{i:05d}" for i in range(13)]
    nm = {T: "FC Barcelona", **{o: f"Club {i}" for i, o in enumerate(others)}}
    def gen(teams, start, step):
        out = []; i = 0
        for h in teams:
            for a in teams:
                if h == a: continue
                n[0] += 1; d = start + timedelta(days=int(i * step)); i += 1
                out.append({"id": f"m_{n[0]:06d}", "date": d.isoformat(), "time_utc": d.isoformat() + "T15:00:00Z", "status": "finished",
                            "home_team": {"id": h, "name": nm[h]}, "away_team": {"id": a, "name": nm[a]}, "score_home": 1, "score_away": 1})
        return out
    lea = {"2024/2025": gen([T] + others, date(2024, 8, 10), 1), "2025/2026": gen([T] + others, date(2025, 8, 9), 1)}
    ucl = {"2025/2026": gen([T] + others[:3], date(2025, 9, 15), 3)}
    L = [{"id": "l_LEAGUE", "name": "La Liga", "country_code": "ESP", "seasons": list(lea)},
         {"id": "l_CHAMPS", "name": "UEFA Champions League", "country_code": "INT", "seasons": list(ucl)},
         {"id": "l_CUPXXX", "name": "Copa", "country_code": "ESP", "seasons": ["2025/2026"]}]
    def fake(path, params=None, ttl=0):
        if path == "/v1/leagues": return {"leagues": L}
        if path == "/v1/leagues/l_LEAGUE/matches": return {"matches": lea[params["season"]]}
        if path == "/v1/leagues/l_CHAMPS/matches": return {"matches": ucl[params["season"]]}
        if path == "/v1/leagues/l_CUPXXX/matches": return {"matches": []}
        if path.startswith("/v1/teams/"): return {"id": path.split("/")[3], "image_url": "https://cdn.pitchapi.dev/teams/83.webp"}
        if path.endswith("/stats"): return {"periods": [{"period": "All", "groups": [{"items": [{"title": "Corners", "key": "corners", "home": "6", "away": "3"}]}]}]}
        if path.endswith("/events"): return {"events": []}
        raise AssertionError(path)
    monkeypatch.setattr(pc, "get", fake); monkeypatch.setattr(service, "BLOCK_INDEX", True)
    service._idx.update(state="idle", teams={}, leagues=[], error=None)
    r = service.search_teams("barce"); assert r["ready"] and r["teams"][0]["id"] == T and "La Liga" in r["teams"][0]["competitions"]
    out = service.team50(T, "main")
    assert out["data"]["n"] == 50 and out["competitions_used"] == ["La Liga", "UEFA Champions League"]
    assert out["team"]["logo"].endswith("83.webp")
    rows = out["data"]["rows"]; assert len(rows) == 50 and rows[0]["date"] >= rows[-1]["date"] and rows[0]["opp"].startswith("Club")
    assert {x["comp"] for x in rows} == {"La Liga", "UEFA Champions League"} and rows[0]["corners"] == [6, 3] or rows[0]["corners"] == [3, 6]
    assert out["data"]["metrics"]["corners"]["n"] == 50 and out["data"]["comp_results"]["champions_league"]["D"] > 0

    # مفضلة: التحليل عبر المسابقات المحفوظة بدون فهرس الفرق
    service._idx.update(state="idle", teams={}, leagues=[], error=None); monkeypatch.setattr(service, "BLOCK_INDEX", False)
    out2 = service.team50(T, "main", comps=r["teams"][0]["comp_ids"], name="FC Barcelona")
    assert out2["data"]["n"] == 50 and out2["team"]["name"] == "FC Barcelona" and service._idx["state"] == "idle"
    assert "l_CHAMPS" in r["teams"][0]["comp_ids"]


def test_big5_ucl_detection_and_logos(monkeypatch):
    L = lambda **k: k
    assert service.big5_label(L(name="x", image_url="https://cdn.pitchapi.dev/leagues/47.webp")) == "Premier League"
    assert service.big5_label(L(name="LaLiga", country_code="ESP")) == "LaLiga"
    assert service.big5_label(L(name="Premier League", country_code="RUS")) is None
    assert service.big5_label(L(name="Bundesliga", country_code="AUT")) is None
    assert service.is_ucl(L(name="UEFA Champions League")) and service.is_ucl(L(name="x", image_url="https://cdn.pitchapi.dev/leagues/42.webp"))
    assert not service.is_ucl(L(name="AFC Champions League")) and not service.is_ucl(L(name="CAF Champions League"))
    monkeypatch.setattr(pc, "get", lambda path, params=None, ttl=0: {"image_url": "https://cdn/" + path[-6:] + ".webp"})
    r = service.logos(["t_AAAAAA", "bad", "t_BBBBBB"]); assert set(r) == {"t_AAAAAA", "t_BBBBBB"} and r["t_AAAAAA"].endswith("AAAAAA.webp")
    service._idx.update(state="ready", error=None, leagues=[{"id": "l_PL0000", "name": "Premier League", "country_code": "ENG"}, {"id": "l_OTHER0", "name": "Other", "country_code": "XXX"}],
                        teams={"t_AAAAAA": {"name": "Arsenal", "comps": {"l_PL0000": 30}}, "t_BBBBBB": {"name": "Nobody", "comps": {"l_OTHER0": 30}}})
    b = service.search_teams("", True); assert [t["name"] for t in b["teams"]] == ["Arsenal"] and b["teams"][0]["big5"] == "Premier League"
    assert service.search_teams("nob")["teams"][0]["big5"] is None

def test_famous_matches(monkeypatch):
    for n, want in [("Real Madrid", True), ("Atlético Madrid", True), ("FC Barcelona", True), ("Paris Saint-Germain", True), ("Internacional", False),
                    ("Brighton", False), ("Inter Milan", True), ("Bayern München", True)]:
        assert service.is_famous(n) is want, n
    T = lambda i, n: {"id": f"t_{i:06d}", "name": n}
    ms = [{"id": "m_000001", "league": {"id": "l_PL0000"}, "home_team": T(1, "Brighton"), "away_team": T(2, "Bournemouth"), "time_utc": "2026-10-12T14:00:00Z"},
          {"id": "m_000002", "league": {"id": "l_LIGA00"}, "home_team": T(3, "Real Madrid"), "away_team": T(4, "Getafe"), "time_utc": "2026-10-12T19:00:00Z"},
          {"id": "m_000003", "league": {"id": "l_UCL000"}, "home_team": T(5, "Bayern München"), "away_team": T(6, "Juventus"), "time_utc": "2026-10-12T20:00:00Z"},
          {"id": "m_000004", "league": {"id": "l_UCL000"}, "home_team": T(7, "Club X"), "away_team": T(8, "Club Y"), "time_utc": "2026-10-12T17:00:00Z"}]
    L = [{"id": "l_PL0000", "name": "Premier League", "country_code": "ENG"}, {"id": "l_LIGA00", "name": "LaLiga", "country_code": "ESP"}, {"id": "l_UCL000", "name": "UEFA Champions League", "country_code": "INT"}]
    monkeypatch.setattr(pc, "get", lambda path, params=None, ttl=0: {"matches": ms} if path.startswith("/v1/date/") else {"leagues": L})
    r = service.famous_matches("2026-10-12")["matches"]
    assert [m["id"] for m in r] == ["m_000003", "m_000002", "m_000004"]        # 9 > 4 > 3 ؛ مباراة برايتون مستبعدة
    assert r[1]["badges"] == {"famous": ["Real Madrid"], "ucl": False, "big5": "LaLiga"}
    from app.main import app
    c = app.test_client(); assert c.get("/api/famous?date=bad").status_code == 400 and c.get("/api/famous?date=2026-10-12").json["matches"][0]["score"] == 9


def test_xg_in_fit_and_goals_markets(monkeypatch):
    from app import model as M, backtest, xg as xgm
    random.seed(21); ts = [f"t{i}" for i in range(12)]; st = {t: 0.5 + i * 0.15 for i, t in enumerate(ts)}
    rows, X, i = [], {}, 0
    for _ in range(3):
        for h in ts:
            for a in ts:
                if h == a: continue
                lh, la = 1.3 * st[h] / st[a] ** .5, st[a] / st[h] ** .5
                rows.append((date(2025, 1, 1) + timedelta(days=i // 3), h, a, _pois(lh), _pois(la), f"m{i}")); X[f"m{i}"] = (lh, la); i += 1
    ref = date(2027, 1, 1)
    def err(alpha):
        m = M.fit(rows, ref, xg=X, alpha=alpha); tr = {t: st[t] for t in ts}
        mean = sum(m["att"].values()) / len(ts); mt = sum(tr.values()) / len(ts)
        return sum((m["att"][t] / mean - tr[t] / mt) ** 2 for t in ts)
    assert err(1.0) < err(0.0)                        # xG المثالي أدق في استرجاع القوة الحقيقية
    assert M.fit(rows, ref, xg=None, alpha=1.0)["n"] == len(rows)   # بلا xG: لا أثر ولا خطأ
    r = backtest.market_eval(rows, test_days=300, min_train=150, xi=0.0019, use_dc=True)
    assert r["over25"]["n"] > 100 and 0 < r["over25"]["brier"] < 0.5 and r["btts"]["baseline_log_loss"] > 0
    t = backtest.tune(rows, test_days=300, min_train=150, xg=X)
    assert t and t["alpha"] in (0.0, 0.5, 1.0) and t["log_loss"] <= t["dc_log_loss"] + 1e-9


def test_counts_engine_and_board(monkeypatch):
    from app import counts, xg as xgm
    random.seed(8); ts = [f"t_{i:06d}" for i in range(14)]; st = {t: 0.6 + 0.1 * i for i, t in enumerate(ts)}
    n = [0]; S = {}; seasons = {"2024/2025": [], "2025/2026": []}
    for sn, start in (("2024/2025", date(2024, 8, 10)), ("2025/2026", date(2025, 8, 9))):
        i = 0
        for h in ts:
            for a in ts:
                if h == a: continue
                n[0] += 1; mid = f"m_{n[0]:06d}"; d = start + timedelta(days=int(i * 1.2)); i += 1
                pz = st[h] * st[a]                                       # "إيقاع" كل فريق يؤثر في مجموع المباراة
                lh, la = 5.0 * pz * 1.1, 5.0 * pz * 0.9
                lc, ld = 4.8 * pz * 1.15, 4.8 * pz * 0.85
                S[mid] = (_pois(lh), _pois(la), _pois(1.6), _pois(1.5), _pois(11), _pois(11), _pois(lc * .45), _pois(ld * .45), _pois(lc * .55), _pois(ld * .55))
                seasons[sn].append({"id": mid, "date": d.isoformat(), "time_utc": d.isoformat() + "T15:00:00Z", "status": "finished", "home_team": {"id": h, "name": h},
                                    "away_team": {"id": a, "name": a}, "score_home": _pois(1.4 * st[h]), "score_away": _pois(1.1 * st[a])})
    item = lambda t, k, a, b: {"title": t, "key": k, "home": str(a), "away": str(b)}
    def fake(path, params=None, ttl=0):
        if path == "/v1/leagues": return {"leagues": [{"id": "l_LEAGUE", "name": "T", "country_code": "TST", "seasons": list(seasons)}]}
        if path == "/v1/leagues/l_LEAGUE/matches": return {"matches": seasons[params["season"]]}
        if path == "/v1/matches/m_TARGET": return {"id": "m_TARGET", "league": {"id": "l_LEAGUE", "name": "T"}, "home_team": {"id": ts[13], "name": "H"},
                "away_team": {"id": ts[0], "name": "A"}, "date": "2026-10-20", "time_utc": "2026-10-20T15:00:00Z"}
        if path.endswith("/events"): return {"events": []}
        if path.endswith("/stats"):
            a = S[path.split("/")[3]]
            return {"periods": [{"period": "All", "groups": [{"items": [item("Shots on target", "shots_on_target", a[0], a[1]), item("Yellow cards", "yellow_cards", a[2], a[3]),
                                                                      item("Total shots", "total_shots", a[4] + 8, a[5] + 8), item("Corners", "corners", a[6] + a[8], a[7] + a[9])]}]},
                                {"period": "1st Half", "groups": [{"items": [item("Corners", "corners", a[6], a[7])]}]},
                                {"period": "2nd Half", "groups": [{"items": [item("Corners", "corners", a[8], a[9])]}]}]}
        raise AssertionError(path)
    monkeypatch.setattr(pc, "get", fake); monkeypatch.setattr(service, "BLOCK_TUNE", True); monkeypatch.setattr(xgm, "BLOCK", True)
    xgm.STATS.clear(); xgm.STORE.clear(); xgm.MISSING.clear(); xgm._state.clear(); service._grades.clear()
    b = service.board_match("m_TARGET")
    sot = b["metrics"]["sot"]; assert sot["available"] and sot["exp_home"] > sot["exp_away"] and sot["n_rows"] >= 120
    c = b["metrics"]["corners"]; assert c["available"] and c["exp_home"] > c["exp_away"] and b["metrics"]["corners_1h"]["available"] and b["metrics"]["corners_2h"]["available"]
    assert abs(c["race"]["home"] + c["race"]["draw"] + c["race"]["away"] - 100) < 0.5 and c["race"]["home"] > c["race"]["away"] and c["race"]["hist"]["home"]["n"] >= 8
    hs = b["half_split"]; assert hs and 35 < hs["league"] < 55 and hs["n"] >= 30                    # الشوط الأول ≈ 45% بحسب البيانات المولّدة
    assert b["grades"]["corners_race"]["grade"] in ("جيد", "مقبول", "ضعيف") and "corners_1h_race" in b["grades"]
    assert b["grades"]["sot"]["grade"] == "جيد"                       # الفروق الحقيقية في قوة الفرق يلتقطها النموذج
    ev = b["events"]; keys = {e["group"] for e in ev}; assert {"result", "goals", "sot", "corners", "corners_1h", "yellow"} <= keys
    one = next(e for e in ev if e["group"] == "goals" and e["label"] == "المجموع أكثر من 2.5"); assert one["call"] in ("نعم", "محايد", "لا", "غير مؤكد") and 0 <= one["p"] <= 100 and one["base"] is not None
    pair = [e for e in ev if e["group"] == "sot" and e["label"].startswith("المجموع") and e["label"].endswith("7.5")]; assert len(pair) == 2 and abs(pair[0]["p"] + pair[1]["p"] - 100) < 0.2
    assert any(e["label"].endswith("الأكثر عددًا") for e in ev if e["group"] == "corners") and any(g["key"] == "corners_1h" and g["available"] for g in b["groups"])
    assert b["picks"] and all(p["p"] >= 58 and p["grade"] in ("جيد", "مقبول") for p in b["picks"]) and b["picks"] == sorted(b["picks"], key=lambda x: -x["score"])
    assert b["metrics"]["goals"]["lines"][1]["over"] + 0 <= 100 and "ليس" in b["disclaimer"] or "لا يعني" in b["disclaimer"]


def test_standings_table_and_split(monkeypatch):
    from app import xg as xgm
    T = lambda i, n: {"id": f"t_{i:06d}", "name": n}
    A, B, C = T(1, "A"), T(2, "B"), T(3, "C")
    M = lambda i, d, h, a, gh, ga: {"id": f"m_{i:06d}", "date": d, "time_utc": d + "T15:00:00Z", "status": "finished", "home_team": h, "away_team": a, "score_home": gh, "score_away": ga}
    ms = [M(1, "2026-08-10", A, B, 2, 0), M(2, "2026-08-11", B, C, 1, 1), M(3, "2026-08-17", C, A, 0, 3), M(4, "2026-08-18", B, A, 2, 2), M(5, "2026-08-24", A, C, 1, 2),
          {**M(6, "2026-12-01", C, B, 0, 0), "score_home": None, "score_away": None}]
    S = {"m_000001": (10, 4, 2, 1, 6, 2), "m_000002": (8, 8, 3, 2, 5, 5), "m_000003": (5, 14, 1, 0, 2, 9)}
    item = lambda t, k, a, b: {"title": t, "key": k, "home": str(a), "away": str(b)}
    def fake(path, params=None, ttl=0):
        if path == "/v1/leagues": return {"leagues": [{"id": "l_TABLE0", "name": "Test", "country_code": "TST", "seasons": ["2026/2027", "2025/2026"]}]}
        if path == "/v1/leagues/l_TABLE0/matches": return {"matches": ms if params["season"] == "2026/2027" else []}
        if path.endswith("/stats"):
            a = S.get(path.split("/")[3])
            if not a: return {}
            return {"periods": [{"period": "All", "groups": [{"items": [item("Total shots", "total_shots", a[0], a[1]), item("Yellow cards", "yellow_cards", a[2], a[3]), item("Corner kicks", "corners", a[4], a[5])]}]}]}
        if path.endswith("/events"): return {"events": []}
        raise AssertionError(path)
    monkeypatch.setattr(pc, "get", fake); monkeypatch.setattr(xgm, "BLOCK", True); xgm.STATS.clear(); xgm.MISSING.clear(); xgm._state.clear()
    r = service.standings("l_TABLE0"); t = {x["name"]: x for x in r["table"]}
    assert r["season"] == "2026/2027" and r["played"] == 5 and r["through"] == "2026-08-24"       # المباراة غير المنتهية لا تُحسب
    assert [x["name"] for x in r["table"]] == ["A", "C", "B"] and [x["pos"] for x in r["table"]] == [1, 2, 3]
    a = t["A"]["all"]; assert (a["p"], a["w"], a["d"], a["l"], a["gf"], a["ga"], a["gd"], a["pts"]) == (4, 2, 1, 1, 8, 4, 4, 7)
    assert (t["A"]["home"]["w"], t["A"]["home"]["l"], t["A"]["away"]["w"], t["A"]["away"]["d"]) == (1, 1, 1, 1)
    assert t["C"]["all"]["pts"] == 4 and t["B"]["all"]["pts"] == 2 and t["A"]["form"] == ["W", "W", "D", "L"]
    sa = t["A"]["stats"]; assert sa["shots"] == {"for": 12.0, "against": 4.5, "n": 2} and sa["corners"] == {"for": 7.5, "against": 2.0, "n": 2}
    assert t["B"]["stats"]["yellow"]["n"] == 2 and "xg" not in sa
    assert (r["summary"]["home_win_pct"], r["summary"]["draw_pct"], r["summary"]["away_win_pct"]) == (20.0, 40.0, 40.0)
    assert service.standings("l_TABLE0", "2025/2026")["played"] == 0


def test_espn_adapter_fills_corners_without_overriding(monkeypatch):
    from app import espn, xg as xgm, last50
    assert espn.norm("Manchester Utd") == espn.norm("Manchester United") and espn.norm("Atlético de Madrid") == espn.norm("Atletico Madrid")
    assert espn.norm("FC Bayern München") == "bayern munich" and espn.norm("Brighton & Hove Albion") == "brighton"
    last50.NAMES.update({"t_AAAAAA": "Arsenal FC", "t_BBBBBB": "Manchester United", "t_CCCCCC": "Chelsea"})
    SB = {"events": [{"id": "e1", "date": "2026-08-16T14:00Z", "status": {"type": {"completed": True}},
                      "competitions": [{"competitors": [{"homeAway": "home", "team": {"displayName": "Arsenal"}}, {"homeAway": "away", "team": {"displayName": "Manchester Utd"}}]}]},
                     {"id": "e2", "date": "2026-08-23T14:00Z", "status": {"type": {"completed": False}},
                      "competitions": [{"competitors": [{"homeAway": "home", "team": {"displayName": "Chelsea"}}, {"homeAway": "away", "team": {"displayName": "Arsenal"}}]}]}]}
    SUM = {"boxscore": {"teams": [{"homeAway": "home", "statistics": [{"name": "wonCorners", "displayValue": "7"}, {"name": "totalShots", "displayValue": "15"}, {"name": "yellowCards", "displayValue": "9"}]},
                                  {"homeAway": "away", "statistics": [{"name": "wonCorners", "displayValue": "3"}, {"name": "totalShots", "displayValue": "9"}, {"name": "yellowCards", "displayValue": "1"}]}]}}
    def fake(url, ttl=0): return SB if "scoreboard" in url else SUM if "summary?event=e1" in url else None
    monkeypatch.setattr(espn, "_get", fake); xgm.STATS.clear(); xgm.STATS["m_000001"] = {"shots": (20.0, 5.0)}   # PitchAPI عنده تسديدات
    hist = [(date(2026, 8, 16), "t_AAAAAA", "t_BBBBBB", 2, 1, "m_000001"), (date(2026, 8, 23), "t_CCCCCC", "t_AAAAAA", 0, 0, "m_000002")]
    n = espn.enrich("eng.1", hist, date(2026, 9, 1))
    assert n == 1 and xgm.STATS["m_000001"]["corners"] == (7.0, 3.0) and xgm.STATS["m_000001"]["shots"] == (20.0, 5.0)     # لا يستبدل PitchAPI
    assert xgm.STATS["m_000001"]["yellow"] == (9.0, 1.0) and "m_000002" not in xgm.STATS                                      # المباراة غير المنتهية تُتجاهل
    monkeypatch.setattr(espn, "_get", lambda url, ttl=0: None); xgm.STATS.pop("m_000001"); assert espn.enrich("eng.1", hist, date(2026, 9, 1)) == 0   # فشل صامت


def test_parse_stats_halves_and_race_math():
    from app import last50, counts
    P = lambda name, c: {"period": name, "groups": [{"items": [{"title": "Corner kicks", "key": "corners", "home": str(c[0]), "away": str(c[1])}]}]}
    d = {"periods": [P("1st Half", (3, 1)), P("All", (7, 4)), P("2nd Half", (4, 3))]}       # ترتيب مقلوب عمدًا
    r = last50.parse_stats(d); assert r["corners"] == (7.0, 4.0) and r["corners_1h"] == (3.0, 1.0) and r["corners_2h"] == (4.0, 3.0)
    assert last50.parse_stats({"periods": [P("1st Half", (3, 1))]}).get("corners") is None       # شوط واحد لا يُعدّ مجمل المباراة
    assert last50.parse_stats({"periods": [P("Match", (9, 2))]})["corners"] == (9.0, 2.0)
    pw, pd_, pl = counts.race(6.0, 4.0, 0.1); assert abs(pw + pd_ + pl - 1) < 1e-9 and pw > pl > 0 and pd_ > 0
    a, b, c = counts.race(5.0, 5.0, 0.1); assert abs(a - c) < 1e-9                                   # تماثل
    assert counts.race(6.0, 4.0, 0.0)[0] > counts.race(5.0, 4.0, 0.0)[0]


def test_verdict_rules_and_events():
    v = service._verdict
    assert v(70, 50, "جيد")["call"] == "نعم" and v(63, 50, "جيد")["call"] == "نعم" and v(63, 50, "مقبول")["call"] == "محايد" and v(66, 50, "مقبول")["call"] == "نعم"
    assert v(58, 50, "جيد")["call"] == "محايد" and v(52, 50, "جيد")["call"] == "لا" and v(40, 50, "جيد")["why"] == "الأرجح ألا يحدث"
    assert v(80, 50, "ضعيف")["call"] == "لا" and "لا يتفوق" in v(80, 50, "ضعيف")["why"]            # سوق ضعيف: لا اعتماد مهما ارتفع الاحتمال
    assert v(80, 50, None)["call"] == "غير مؤكد" and v(80, 50, "جيد", ready=False)["call"] == "انتظر"
    assert "المعتاد" in v(63, 62, "جيد")["why"]


def test_cache_singleflight_errors_and_gzip(monkeypatch):
    import threading, gzip as gz, json, time
    from app import cache
    c = cache.TTLCache(3); calls = []
    def slow(): calls.append(1); time.sleep(0.15); return "v"
    ts = [threading.Thread(target=lambda: c.get_or("k", 60, slow)) for _ in range(6)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert len(calls) == 1 and c.get_or("k", 60, slow) == "v" and len(calls) == 1          # حساب واحد لعدة طلبات متزامنة
    def boom(): raise ValueError("x")
    for _ in range(2):
        try: c.get_or("bad", 60, boom)
        except ValueError: pass
    assert "bad" not in c.d                                                               # الخطأ لا يُخزَّن
    for i in range(6): c.get_or(f"k{i}", 60, lambda: i)
    assert len(c.d) <= 3                                                                  # حد أقصى للحجم
    n = [0]
    def fake(path, params=None, ttl=0):
        n[0] += 1; return {"leagues": [{"id": f"l_{i:06d}", "name": "League " + "x" * 40, "country_code": "TST", "seasons": ["2026"]} for i in range(60)]}
    monkeypatch.setattr(pc, "get", fake)
    from app.main import app
    cl = app.test_client(); plain = cl.get("/api/leagues"); gzr = cl.get("/api/leagues", headers={"Accept-Encoding": "gzip"})
    assert plain.headers.get("Content-Encoding") is None and gzr.headers["Content-Encoding"] == "gzip" and len(gzr.data) < len(plain.data) / 2
    assert json.loads(gz.decompress(gzr.data)) == plain.json                              # نفس المحتوى تمامًا
    h = cl.get("/", headers={"Accept-Encoding": "gzip"}); assert h.headers["Content-Encoding"] == "gzip" and "no-cache" in h.headers["Cache-Control"] and b"<html" in gz.decompress(h.data)
    assert cl.get("/api/health", headers={"Accept-Encoding": "gzip"}).json["ok"] is True   # الردود الصغيرة تبقى سليمة

# شكل حقيقي (مقتطف من scoreboard ESPN الحي لمباراة Chelsea at Everton)
ESPN_EVENT = {"id": "401878771", "date": "2026-10-17T11:30Z", "competitions": [{"status": {"type": {"state": "pre", "completed": False}},
    "venue": {"id": "10318", "fullName": "Hill Dickinson Stadium", "address": {"city": "Liverpool", "country": "England"}},
    "competitors": [{"homeAway": "home", "form": "DWWDD", "records": [{"summary": "2-4-0"}], "team": {"displayName": "Everton"}},
                    {"homeAway": "away", "form": "DLDWL", "records": [{"summary": "3-1-2"}], "team": {"displayName": "Chelsea"}}],
    "odds": [{"provider": {"displayName": "DraftKings"}, "drawOdds": {"moneyLine": 275},
              "total": {"over": {"open": {"line": "o2.5", "odds": "-165"}, "close": {"line": "o2.5", "odds": "-145"}}, "under": {"open": {"line": "u2.5", "odds": "+120"}, "close": {"line": "u2.5", "odds": "+120"}}},
              "moneyline": {"home": {"open": {"odds": "+175"}, "close": {"odds": "+215"}}, "away": {"open": {"odds": "+130"}, "close": {"odds": "+115"}}, "draw": {"open": {"odds": "+275"}, "close": {"odds": "+275"}}}}]}]}

def test_espn_real_shape_facts_and_market_math():
    from app import espn
    assert espn._dec("+215") == 3.15 and espn._dec("-145") == 1.69 or abs(espn._dec("-145") - 1.69) < 0.01
    f = espn.facts(ESPN_EVENT); mk = f["market"]
    assert f["venue"] == {"name": "Hill Dickinson Stadium", "city": "Liverpool", "country": "England"} and f["form"] == {"home": "DWWDD", "away": "DLDWL"} and f["state"] == "pre"
    assert mk["provider"] == "DraftKings" and mk["ml"]["dec"] == {"home": 3.15, "draw": 3.75, "away": 2.15}
    pr = mk["ml"]["prob"]; assert abs(sum(pr.values()) - 100) < 0.2 and pr == {"home": 30.3, "draw": 25.4, "away": 44.3} and abs(mk["ml"]["overround"] - 4.9) < 0.1     # هامش الشركة أُزيل
    assert mk["total"]["line"] == 2.5 and abs(mk["total"]["over"]["prob"] - 56.6) < 0.2 and mk["ml_open"]["prob"]["home"] > 0 and mk["ml_open"]["prob"]["home"] != pr["home"]   # الفتح غير الإغلاق
    assert espn.facts({**ESPN_EVENT, "competitions": [{**ESPN_EVENT["competitions"][0], "odds": []}]})["market"] is None
    assert espn.find_event.__name__ == "find_event"

def test_market_view_and_verdict_downgrade(monkeypatch):
    from app import espn
    v = service._verdict
    assert v(70, 50, "جيد", True, 69)["call"] == "نعم" and v(70, 50, "جيد", True, 55)["call"] == "محايد" and "فارق" in v(70, 50, "جيد", True, 55)["why"]      # السوق يخالف: لا «نعم»
    assert v(70, 50, "جيد", True, None)["call"] == "نعم" and v(80, 50, "ضعيف", True, 80)["call"] == "لا"
    P = {"match": {"id": "m_AAAAAA", "home": "Everton", "away": "Chelsea", "league": "PL"}, "prediction": {"home": 25, "draw": 25, "away": 50, "markets": {"ou": {"2.5": {"over": 70.0, "under": 30.0}}}}}
    monkeypatch.setattr(service, "predict_match", lambda mid: P)
    monkeypatch.setattr(service, "market_facts", lambda mid: espn.facts(ESPN_EVENT))
    r = service.market_view("m_AAAAAA"); c = {x["label"]: x for x in r["compare"]}
    assert r["available"] and c["فوز Chelsea"]["gap"] == 5.7 and c["المجموع أكثر من 2.5"]["gap"] == 13.4 and "المجموع أكثر من 2.5" in r["read"]       # فارق ≥ 8 يُحذَّر منه
    assert r["venue"]["city"] == "Liverpool" and "DraftKings" in r["source"]
    mp = service._market_probs(espn.facts(ESPN_EVENT)["market"], "Everton", "Chelsea"); assert mp["فوز Everton"] == 30.3 and mp["المجموع أقل من 2.5"] == 43.4 and mp["Everton أو تعادل"] == 55.7
    monkeypatch.setattr(service, "market_facts", lambda mid: None); assert service.market_view("m_AAAAAA")["available"] is False

def test_data_status_report(monkeypatch):
    from app import xg as xgm
    L = [{"id": "l_PL0000", "name": "Premier League", "country_code": "ENG", "seasons": ["2026/2027"]}, {"id": "l_OTHER0", "name": "Other League", "country_code": "XXX", "seasons": ["2026/2027"]}]
    ms = [{"id": f"m_{i:06d}", "date": f"2026-0{1 + i % 9}-10", "home_team": {"id": "t_AAAAAA", "name": "A"}, "away_team": {"id": "t_BBBBBB", "name": "B"}, "score_home": 1, "score_away": 0} for i in range(130)]
    monkeypatch.setattr(pc, "get", lambda path, params=None, ttl=0: {"leagues": L} if path == "/v1/leagues" else {"matches": ms})
    monkeypatch.setattr(xgm, "BLOCK", True); xgm.STATS.clear(); xgm.MISSING.clear(); xgm._state.clear()
    xgm.STATS.update({m["id"]: {"shots": (10.0, 5.0)} for m in ms}); xgm.STATS.update({ms[0]["id"]: {"shots": (10.0, 5.0), "corners": (5.0, 3.0)}})
    r = service.data_status("l_PL0000")
    assert r["matches"] == 130 and r["espn"]["covered"] and r["espn"]["slug"] == "eng.1" and r["metrics"]["shots"]["usable"] and r["metrics"]["shots"]["pct"] == 100
    assert not r["metrics"]["corners"]["usable"] and r["metrics"]["corners"]["n"] == 1 and "ESPN" in r["metrics"]["corners"]["why"]
    o = service.data_status("l_OTHER0"); assert not o["espn"]["covered"] and "لا يغطي" in o["metrics"]["corners"]["why"]            # يشرح سبب النقص بصدق
    ids = [("m_x1", "a", "b")]; xgm.STATS.clear(); xgm.MISSING.clear()
    hist = [(date(2026, 1, 1) + timedelta(days=i), "a", "b", 1, 0, f"m_{i:06d}") for i in range(5)]
    order = []
    monkeypatch.setattr(xgm.last50, "_match_stats", lambda mid, h, a: (order.append(mid), {"shots": (1.0, 1.0)})[1]); xgm._state.clear(); xgm.SLUG.clear()
    xgm.ensure("l_X", hist, date(2026, 6, 1)); assert order[0] == "m_000004"                                                         # الأحدث أولًا

def test_grades_progressive_not_gated_and_resilient(monkeypatch):
    """يعيد أخطاء «انتظر» الأبدي: (1) انتظار انتهاء تحميل الإحصاءات (2) موت الخيط بصمت (3) إعادة الحساب من الصفر مع كل تحديث."""
    from app import xg as xgm, counts, backtest
    started = []
    class FakeThread:
        def __init__(self, target=None, daemon=None, args=()): self.t = target
        def start(self): started.append(self.t)
    monkeypatch.setattr(service.threading, "Thread", FakeThread)
    xgm._state.clear(); xgm._state["l_G"] = {"running": True, "total": 800, "done": 10}          # التحميل لا يزال جاريًا
    monkeypatch.setattr(backtest, "market_eval", lambda hist, **kw: {"over25": {"baseline_log_loss": 0.70, "log_loss": 0.6985, "n": 300}, "btts": {"baseline_log_loss": 0.70, "log_loss": 0.71, "n": 300}})
    rows = [(date(2026, 1, 1), "a", "b", 1, 1, f"m{i}") for i in range(200)]; monkeypatch.setattr(counts, "rows_for", lambda h, k, r: rows)
    def ev(rows_, with_race=False, **kw):
        if with_race is False and len(started) and ev.fail: raise ZeroDivisionError("x")      # سوق «yellow» يفشل
        return ({"n": 300, "log_loss": 0.6, "baseline_log_loss": 0.7, "log_loss_gain": 0.1, "grade": "جيد"}, {"n": 300, "log_loss": 0.6, "baseline_log_loss": 0.7, "log_loss_gain": 0.1, "grade": "جيد"} if with_race else None)
    ev.fail = False; calls = []
    def ev2(rows_, with_race=False, **kw):
        calls.append(with_race)
        if len(calls) == 4: raise ZeroDivisionError("boom")                                     # رابع سوق يفشل
        return ev(rows_, with_race)
    monkeypatch.setattr(counts, "evaluate_all", ev2)
    g0 = service.get_grades("l_G", [], [], date(2026, 10, 1))
    assert g0 == {} and len(started) == 1 and service.grades_state("l_G")["state"] == "running"   # بدأ فورًا رغم أن التحميل جارٍ، ولم يحجب الطلب
    assert service.get_grades("l_G", [], [], date(2026, 10, 1)) == {} and len(started) == 1       # طلب ثانٍ أثناء العمل: لا تكرار
    started[0]()                                                                                  # تنفيذ العمل الخلفي
    st = service.grades_state("l_G"); g = service.get_grades("l_G", [], [], date(2026, 10, 1))
    assert st["state"] == "done" and st["finished"] == st["total"] == 8 and len(st["errors"]) == 1 and "ZeroDivisionError" in st["errors"][0]    # فشل سوق لم يوقف الباقي
    assert g["goals"]["grade"] == "مقبول" and g["btts"]["grade"] == "ضعيف" and g["corners"]["grade"] == "جيد" and g["corners_race"]["grade"] == "جيد" and "sot_race" in g
    assert sum(1 for k in ("corners", "sot", "shots", "yellow", "fouls", "corners_1h", "corners_2h") if g.get(k) is None) == 1          # سوق واحد فقط None
    assert len(started) == 1                                                                      # بعد الاكتمال لا إعادة حساب مع كل تحديث
    for i in range(300): xgm.STATS[f"z{i}"] = {"shots": (1.0, 1.0)}                                  # بيانات جديدة كثيرة تُنعش الدرجات في الخلفية دون حذف القديمة
    old = service.get_grades("l_G", [], [], date(2026, 10, 1)); assert len(started) == 2 and old["goals"]["grade"] == "مقبول"

def test_events_wait_only_for_untested_market_and_evaluate_all_matches():
    from app import counts
    m = {"sot": {"available": True, "label": "التسديدات على المرمى", "exp_home": 5.0, "exp_away": 4.0, "exp_total": 9.0, "n_rows": 300,
                 "lines": [{"line": 8.5, "over": 70.0, "base": 50.0}], "team_lines": {"home": [{"line": 3.5, "over": 55.0, "base": 50.0}], "away": []}},
         "yellow": {"available": True, "label": "البطاقات الصفراء", "exp_home": 2.0, "exp_away": 2.0, "exp_total": 4.0, "n_rows": 300,
                    "lines": [{"line": 3.5, "over": 70.0, "base": 50.0}], "team_lines": {"home": [], "away": []}}}
    G = {"sot": {"grade": "جيد", "n": 300}}
    run = service._build_events(m, G, None, {}, [], "H", "A", None, "running")[0]
    by = {(e["group"], e["label"]): e for e in run}
    assert by[("sot", "المجموع أكثر من 8.5")]["call"] == "نعم" and by[("yellow", "المجموع أكثر من 3.5")]["call"] == "انتظر"      # sot جاهز الآن، yellow ينتظر دوره
    done = service._build_events(m, G, None, {}, [], "H", "A", None, "done")[0]
    assert {(e["group"], e["label"]): e for e in done}[("yellow", "المجموع أكثر من 3.5")]["call"] == "غير مؤكد"            # انتهى الاختبار ولا درجة: لا ننتظر للأبد
    import random
    random.seed(3); rows = [(date(2025, 1, 1) + timedelta(days=i // 3), f"t{i % 12}", f"t{(i * 5 + 1) % 12}", random.randint(2, 9), random.randint(1, 7), f"m{i}") for i in range(700) if i % 12 != (i * 5 + 1) % 12]
    t1, r1 = counts.evaluate_all(rows, True); assert t1 == counts.evaluate(rows) and r1 == counts.evaluate_race(rows)         # التدريب الواحد يعطي نفس نتائج التقييمين المنفصلين

def test_diag_endpoint():
    from app.main import app
    r = app.test_client().get("/api/diag").json
    assert r["version"] and isinstance(r["fit_760_rows_ms"], int) and r["server_speed"] and "grades" in r and "stats_loaded" in r
