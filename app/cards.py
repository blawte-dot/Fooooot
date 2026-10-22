"""بطاقات صفراء من أحداث المباريات الفعلية (events). لا بيانات = لا توقع."""
from concurrent.futures import ThreadPoolExecutor
from . import pitch_client as pc, model

def _yellows(match_id, team_id):
    try: evs = pc.get(f"/v1/matches/{match_id}/events", ttl=24 * 3600).get("events") or []
    except pc.PitchError: return None
    if not evs: return None   # لا أحداث = بيانات غير متوفرة وليست "صفر بطاقات"
    return sum(1 for e in evs if str(e.get("event_type", "")).lower().replace("_", "") == "yellowcard"
               and e.get("team_id") == team_id)

def _collect(hist, team, ref, n):
    ms = sorted([h for h in hist if team in (h[1], h[2]) and h[0] < ref and len(h) > 5], reverse=True)[:n]
    with ThreadPoolExecutor(4) as ex: vals = list(ex.map(lambda h: _yellows(h[5], team), ms))
    return [v for v in vals if v is not None]

def predict(hist, home, away, ref, n=10, min_n=5, k=3.0):
    vh, va = _collect(hist, home, ref, n), _collect(hist, away, ref, n)
    if len(vh) < min_n or len(va) < min_n: return None
    mu = (sum(vh) + sum(va)) / (len(vh) + len(va))
    lh, la = (sum(vh) + k * mu) / (len(vh) + k), (sum(va) + k * mu) / (len(va) + k)
    t = lh + la
    return {"home": {"avg": round(lh, 2), "n": len(vh)}, "away": {"avg": round(la, 2), "n": len(va)}, "total": round(t, 2),
            "lines": {str(l): {"over": model.poisson_over(t, l), "under": 1 - model.poisson_over(t, l)} for l in (2.5, 3.5, 4.5, 5.5)},
            "note": "تقدير من آخر المباريات لكل فريق (بطاقات صفراء فقط). التوزيع أكثر تشتتًا من بواسون، فاعتبره تقريبيًا."}
