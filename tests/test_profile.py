import random
from datetime import date, timedelta
from app import profile as P

def make_hist():
    """دوري تجريبي: A قوي هجوميًا، B دفاعي، C ضعيف هش، وفرق أخرى."""
    random.seed(7); teams = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]; atk = {"A": 2.4, "B": 0.9, "C": 1.0, "D": 1.5, "E": 1.4, "F": 1.2, "G": 1.1, "H": 1.3, "I": 1.0, "J": 0.8}
    dfn = {"A": 0.9, "B": 0.6, "C": 2.2, "D": 1.1, "E": 1.3, "F": 1.2, "G": 1.4, "H": 1.2, "I": 1.5, "J": 1.6}
    hist, d, k = [], date.today() - timedelta(days=300), 0
    for rnd in range(4):
        for h in teams:
            for a in teams:
                if h == a: continue
                d += timedelta(hours=7); k += 1
                gh = max(0, round(random.gauss(atk[h] * dfn[a] * 0.9, 0.8))); ga = max(0, round(random.gauss(atk[a] * dfn[h] * 0.75, 0.8)))
                hist.append((d.replace() if False else date.today() - timedelta(days=300 - k // 4), h, a, gh, ga, f"m{k}", "league", "L"))
    return hist

def test_style_labels():
    assert P.style_label(1.3, 0.8) == "مسيطر (هجوم قوي ودفاع صلب)" and P.style_label(0.8, 0.8).startswith("دفاعي مغلق")
    assert P.style_label(1.3, 1.3).startswith("هجومي مفتوح") and P.style_label(1.0, 1.0) == "متوازن" and P.style_label(1.0, 1.3) == "هش دفاعيًا"

def test_profile_and_matchup():
    hist = make_hist(); ref = date.today() + timedelta(days=1); ctx = P.league_ctx(hist, ref)
    assert ctx and set(ctx["tier"].values()) == {"big", "mid", "small"} and ctx["tier"]["A"] == "big"
    games = lambda t: sorted([h for h in hist if t in (h[1], h[2])], reverse=True)[:50]
    stats = {h[5]: {"corners": (6.0, 4.0), "yellow": (2.0, 1.0), "corners_1h": (3.0, 2.0)} for h in hist}
    pa = P.build_profile("A", "Alpha", games("A"), stats, ctx, ref); pc_ = P.build_profile("C", "Charlie", games("C"), stats, ctx, ref)
    assert pa["style"]["attack"] > 1.12 and pa["style"]["label"].startswith(("هجومي", "مسيطر")) and pc_["style"]["defense"] > 1.12
    assert pa["overall"]["n"] == 50 and sum(x["n"] for x in pa["tiers"]) + 0 <= 50 and pa["tiers"][0]["key"] == "big"
    assert pa["overall"]["cf"] is not None and pa["scores_most_vs"] and "trend" in pa
    mu = P.matchup(pa, pc_)
    assert mu and any("دفاعًا هشًّا" in n for n in mu["notes"]) and mu["facts"]["home_style"]["label"] and mu["corners_expect"]["total"] > 0
    assert P.matchup(None, pa) is None and P.build_profile("Z", "z", [], {}, ctx, ref) is None
