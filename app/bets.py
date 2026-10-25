"""اختيارات الرهان المقترحة بشرط السعر: تُؤخذ «أفضل الأسواق دعمًا» عند Gemini (محصورة بأحداث المحرك الإحصائي وأرقامه)،
وتُقبل فقط إن حكم المحرك «نعم» ووُجد سعر حقيقي منشور (ESPN/DraftKings، عشري) أعلى من الحد الأدنى. لا أسعار مختلقة، ولا ضمان."""
import re

MIN_ODDS = 1.30

def odds_for(label, market, home, away):
    """(السعر العشري، احتمال السوق بعد إزالة الهامش %) لحدث له سعر منشور؛ وإلا (None, None). المتاح: 1X2 وخط مجموع الأهداف المنشور فقط."""
    mk = (market or {}).get("market") or {}; ml, t = mk.get("ml"), mk.get("total")
    if ml and label in (f"فوز {home}", "تعادل", f"فوز {away}"):
        k = "home" if label == f"فوز {home}" else "draw" if label == "تعادل" else "away"
        return (ml.get("dec") or {}).get(k), (ml.get("prob") or {}).get(k)
    m = re.match(r"^المجموع (أكثر|أقل) من (\d+(?:\.\d+)?)$", label)
    if m and t and t.get("line") is not None and abs(float(m.group(2)) - float(t["line"])) < 1e-9:
        side = t.get("over" if m.group(1) == "أكثر" else "under") or {}; return side.get("dec"), side.get("prob")
    return None, None

def judge(ev, market, home, away, min_odds=MIN_ODDS):
    """ev: حدث من المحرك {label,p,grade,call,...}. يُرجع (pick, سبب_الاستبعاد)."""
    odds, fair = odds_for(ev["label"], market, home, away)
    if ev.get("call") != "نعم": return None, f"حكم المحرك «{ev.get('call')}» وليس «نعم»"
    if odds is None: return None, "لا سعر منشور لهذا السوق (المتاح 1X2 وخط المجموع فقط)"
    if not odds > min_odds: return None, f"السعر {odds} ليس أعلى من {min_odds}"
    return {"label": ev["label"], "p": ev["p"], "grade": ev.get("grade"), "odds": odds, "breakeven": round(100 / odds, 1),
            "fair": fair, "gap": None if fair is None else round(ev["p"] - fair, 1), "reason": ev.get("reason", "")}, None

def for_match(mid, pred, ai, board, market):
    m = (pred or {}).get("match") or {}; home, away = m.get("home"), m.get("away")
    if not home or not market or not market.get("market"): return None
    head = {"id": mid, "home": home, "away": away, "league": m.get("league"), "time_utc": m.get("time_utc"), "source": market.get("source")}
    gem, exc = [], []
    for ev in (ai or {}).get("best_supported_markets") or []:
        p, why = judge(ev, market, home, away)
        (gem if p else exc).append(p or {"label": ev.get("label"), "why": why})
    seen = {x["label"] for x in gem}; eng = []
    for ev in sorted([e for e in (board or {}).get("events", []) if e.get("call") == "نعم"], key=lambda e: -e["p"]):
        if ev["label"] in seen: continue
        p, _ = judge(ev, market, home, away)
        if p: eng.append(p)
    return {**head, "gemini": gem, "excluded": exc, "engine": eng[:4], "has_ai": bool(ai)}

def collect(items, min_odds=MIN_ODDS):
    items = [i for i in items if i and (i["gemini"] or i["engine"])]
    key = lambda i: max([x["p"] - x["breakeven"] for x in i["gemini"] + i["engine"]] or [0])
    return {"min_odds": min_odds, "matches": sorted(items, key=key, reverse=True),
            "note": "ليست نصيحة رهان ولا ضمانًا ولا وعدًا بربح. الأسعار من ESPN/DraftKings وللمباريات القادمة في الدوريات الخمسة الكبرى ودوري الأبطال فقط. «نسبة التعادل المالي» أقل احتمال يلزم تحقق الحدث ليتعادل الرهان. الاختيارات من أحداث اجتازت اختبار المحرك الإحصائي، لكن الاحتمالات تقديرية وقد تخطئ، والسوق عادةً أدق."}
