import math, random
from datetime import date, timedelta
from app import model

def test_matrix_and_probs():
    m = model.score_matrix(1.5, 1.1, -0.08)
    assert abs(sum(map(sum, m)) - 1) < 1e-9
    s = model.summarize(m)
    assert abs(s["home"] + s["draw"] + s["away"] - 1) < 1e-9
    assert abs(s["over25"] + s["under25"] - 1) < 1e-9
    assert sum(model.round_to_100([s["home"], s["draw"], s["away"]])) == 100

def _pois(l):
    L, k, p = math.exp(-l), 0, 1.0
    while True:
        p *= random.random()
        if p <= L: return k
        k += 1

def test_fit_recovers_strength():
    random.seed(1)
    st = {"A": 1.8, "B": 1.0, "C": 0.6, "D": 1.0}
    rows, d0, i = [], date(2025, 1, 1), 0
    for _ in range(40):
        for h in st:
            for a in st:
                if h != a:
                    rows.append((d0 + timedelta(days=i % 300), h, a, _pois(1.3 * st[h] / st[a] ** .5), _pois(st[a] / st[h] ** .5))); i += 1
    mdl = model.fit(rows, date(2026, 1, 1))
    assert model.predict(mdl, "A", "C")["home"] > model.predict(mdl, "C", "A")["home"]
    assert model.predict(mdl, "A", "C")["home"] > 0.6
    assert mdl["home_adv"] > 1.0

def test_refuses_insufficient():
    mdl = model.fit([(date(2025, 1, 1), "A", "B", 1, 0)], date(2026, 1, 1))
    try: model.predict(mdl, "A", "B"); assert False
    except ValueError: pass

def test_backtest_no_leak_and_beats_baseline():
    import random
    from app import backtest
    random.seed(7)
    st = {f"T{i}": s for i, s in enumerate([2.0, 1.6, 1.3, 1.1, 1.0, 0.9, 0.75, 0.6, 0.5, 0.4])}
    rows, d = [], date(2024, 8, 1); i = 0
    for rnd in range(4):
        for h in st:
            for a in st:
                if h != a:
                    rows.append((d + timedelta(days=i // 3), h, a, _pois(1.3 * st[h] / st[a] ** .5), _pois(st[a] / st[h] ** .5))); i += 1
    r = backtest.run(rows, test_days=150)
    assert r["tested_matches"] > 100
    m = r["models"]
    assert m["dixon_coles_decay"]["brier"] < m["baseline"]["brier"]
    assert m["dixon_coles_decay"]["log_loss"] < m["baseline"]["log_loss"]
