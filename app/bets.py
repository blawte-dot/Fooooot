"""اختيارات الرهان المقترحة بشرط السعر: تُؤخذ «أفضل الأسواق دعمًا» عند Gemini (محصورة بأحداث المحرك الإحصائي وأرقامه)،
وتُقبل فقط إن حكم المحرك «نعم» ووُجد سعر حقيقي منشور (ESPN/DraftKings، عشري) أعلى من الحد الأدنى. لا أسعار مختلقة، ولا ضمان."""
import re

MIN_ODDS = 1.30
HIGH_P, HIGH_ODDS = 70.0, 1.20      # قسم «احتمال عالٍ»: احتمال المحرك 70% فأكثر وسعر منشور فوق 1.20

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

def _reason(ev, odds, fair):
    """سبب الاختيار من أرقام فعلية فقط: تعليل المحرك/Gemini إن وُجد، ثم مقارنة احتمال المحرك بنسبة التعادل المالي وبالسوق."""
    base = (ev.get("reason") or ev.get("why") or "").strip(); p = ev["p"]; be = round(100 / odds, 1)
    parts = [base] if base else []
    parts.append(f"احتمال المحرك {p}% مقابل {be}% يلزم لتعادل الربح والخسارة عند السعر {odds}" + (" (فوق الحد)." if p > be else " (تحت الحد: السعر لا يعوّض المخاطرة بحسب المحرك)."))
    if fair is not None: parts.append(f"السوق يقدّر {fair}% بعد إزالة الهامش" + ("، والفارق صغير فالتقدير متقارب." if abs(p - fair) < 5 else "، والفارق كبير فقد ينقصنا خبر (إصابة/إيقاف)."))
    return " ".join(parts)

def judge(ev, market, home, away, min_odds=MIN_ODDS):
    """ev: حدث من المحرك {label,p,grade,call,...}. يُرجع (pick, سبب_الاستبعاد)."""
    odds, fair = odds_for(ev["label"], market, home, away)
    if ev.get("call") != "نعم": return None, f"حكم المحرك «{ev.get('call')}» وليس «نعم»"
    if odds is None: return None, "لا سعر منشور لهذا السوق (المتاح 1X2 وخط المجموع فقط)"
    if not odds > min_odds: return None, f"السعر {odds} ليس أعلى من {min_odds}"
    return {"label": ev["label"], "p": ev["p"], "grade": ev.get("grade"), "odds": odds, "breakeven": round(100 / odds, 1),
            "fair": fair, "gap": None if fair is None else round(ev["p"] - fair, 1), "reason": _reason(ev, odds, fair)}, None

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
    high, seen_h = [], set()
    for ev in sorted(list((ai or {}).get("best_supported_markets") or []) + [e for e in (board or {}).get("events", [])], key=lambda e: -(e.get("p") or 0)):
        if ev.get("label") in seen_h or (ev.get("p") or 0) < HIGH_P: continue
        p, _ = judge(ev, market, home, away, HIGH_ODDS)
        if p: high.append(p); seen_h.add(ev["label"])
    return {**head, "gemini": gem, "excluded": exc, "engine": eng[:4], "high": high[:3], "has_ai": bool(ai)}

def collect(items, min_odds=MIN_ODDS):
    items = [i for i in items if i]; hi = sorted([i for i in items if i.get("high")], key=lambda i: -max(x["p"] for x in i["high"]))
    items = [i for i in items if i["gemini"] or i["engine"]]
    key = lambda i: max([x["p"] for x in i["gemini"] + i["engine"]] or [0])      # الأعلى احتمالًا أولًا (السعر فوق الحد دائمًا)
    return {"min_odds": min_odds, "high_p": HIGH_P, "high_odds": HIGH_ODDS, "high": hi, "matches": sorted(items, key=key, reverse=True),
            "note": "ليست نصيحة رهان ولا ضمانًا ولا وعدًا بربح. الأسعار من ESPN/DraftKings وللمباريات القادمة في الدوريات الخمسة الكبرى ودوري الأبطال فقط. «نسبة التعادل المالي» أقل احتمال يلزم تحقق الحدث ليتعادل الرهان. الاختيارات من أحداث اجتازت اختبار المحرك الإحصائي، لكن الاحتمالات تقديرية وقد تخطئ، والسوق عادةً أدق."}
