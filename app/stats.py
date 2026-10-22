"""إحصاءات وصفية حقيقية من النتائج المخزنة (شكل الفريق، المواجهات المباشرة)."""

def _games(hist, team, ref):
    g = []
    for d, h, a, gh, ga, *_ in sorted((x for x in hist if x[0] < ref and team in (x[1], x[2])), reverse=True):
        g.append((d, h == team, gh if h == team else ga, ga if h == team else gh))
    return g

def form(hist, team, ref, n=10, venue=None):
    g = _games(hist, team, ref)
    if venue is not None: g = [x for x in g if x[1] == venue]
    g = g[:n]
    if not g: return None
    pc = lambda f: round(100 * sum(1 for x in g if f(x)) / len(g))
    return {"n": len(g), "form": ["W" if x[2] > x[3] else "D" if x[2] == x[3] else "L" for x in g[:5]],
            "gf": round(sum(x[2] for x in g) / len(g), 2), "ga": round(sum(x[3] for x in g) / len(g), 2),
            "over25": pc(lambda x: x[2] + x[3] > 2.5), "btts": pc(lambda x: x[2] > 0 and x[3] > 0), "clean": pc(lambda x: x[3] == 0)}

def h2h(hist, home, away, ref, n=6):
    ms = sorted((x for x in hist if x[0] < ref and {x[1], x[2]} == {home, away}), reverse=True)[:n]
    if not ms: return None
    rec, out = {"home_wins": 0, "draws": 0, "away_wins": 0}, []
    for d, h, a, gh, ga, *_ in ms:
        hs, as_ = (gh, ga) if h == home else (ga, gh)
        rec["home_wins" if hs > as_ else "draws" if hs == as_ else "away_wins"] += 1
        out.append({"date": d.isoformat(), "score": f"{gh}-{ga}", "home_was_home": h == home})
    return {"n": len(ms), "record": rec, "matches": out}
