"""محوّل ESPN (واجهة غير رسمية وغير موثقة، قد تتغير دون إنذار): يكمّل الركنيات والإحصاءات الناقصة للمباريات المنتهية.
يفشل بصمت: أي خلل يعني ببساطة أن الإحصاءات لا تُكمَّل. لا يستبدل أبدًا ما جاء من PitchAPI."""
import re, time, threading, unicodedata, difflib
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
import requests
from . import xg as xgm, last50

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
SLUGS = {"Premier League": "eng.1", "LaLiga": "esp.1", "Bundesliga": "ger.1", "Serie A": "ita.1", "Ligue 1": "fra.1", "UCL": "uefa.champions"}
_cache, _lock, _sem = {}, threading.Lock(), threading.Semaphore(4)
ALIAS = {"manchester utd": "manchester united", "man utd": "manchester united", "man city": "manchester city", "wolverhampton wanderers": "wolves",
         "wolverhampton": "wolves", "internazionale": "inter", "inter milan": "inter", "paris saint germain": "psg", "paris sg": "psg", "atletico de madrid": "atletico madrid",
         "bayern munchen": "bayern munich", "borussia monchengladbach": "monchengladbach", "newcastle united": "newcastle", "tottenham hotspur": "tottenham",
         "brighton and hove albion": "brighton", "west ham united": "west ham", "leeds united": "leeds", "nottingham forest": "nottm forest", "ac milan": "milan"}
_DROP = {"fc", "cf", "afc", "sc", "ac", "cd", "ud", "sd", "rc", "rcd", "ssc", "as", "us", "the", "1", "de", "calcio", "club"}
KEYMAP = [("corners", ["corner"]), ("sot", ["shotsontarget", "shotongoal"]), ("shots", ["totalshots", "shotstotal"]),
          ("yellow", ["yellowcard"]), ("fouls", ["foul"])]

def norm(name):
    n = "".join(c for c in unicodedata.normalize("NFKD", name or "") if not unicodedata.combining(c)).lower()
    n = re.sub(r"[^a-z0-9 ]", " ", n.replace("&", " and ")); n = " ".join(n.split())
    n = ALIAS.get(n, n)
    base = " ".join(w for w in n.split() if w not in _DROP) or n
    return ALIAS.get(base, base)

def _get(url, ttl=3600):
    with _lock:
        hit = _cache.get(url)
        if hit and hit[0] > time.time(): return hit[1]
    try:
        with _sem: r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 (MatchPredict)"})
        data = r.json() if r.status_code == 200 else None
    except Exception: data = None
    if data is not None:
        with _lock: _cache[url] = (time.time() + ttl, data)
    return data

def scoreboard(slug, d0, d1):
    out, cur = [], d0
    while cur <= d1:
        end = min(cur + timedelta(days=44), d1)
        j = _get(f"{BASE}/{slug}/scoreboard?dates={cur:%Y%m%d}-{end:%Y%m%d}&limit=500", ttl=6 * 3600) or {}
        for e in j.get("events", []):
            try:
                c = e["competitions"][0]; tm = {x["homeAway"]: x["team"].get("displayName") or x["team"].get("name") for x in c["competitors"]}
                if e.get("status", {}).get("type", {}).get("completed"):
                    out.append({"id": e["id"], "date": date.fromisoformat(e["date"][:10]), "home": tm["home"], "away": tm["away"]})
            except Exception: continue
        cur = end + timedelta(days=1)
    return out

def summary_for(slug, event_id):
    j = _get(f"{BASE}/{slug}/summary?event={event_id}", ttl=24 * 3600) or {}
    res = {}
    try:
        teams = j["boxscore"]["teams"]; byside = {t.get("homeAway"): t for t in teams}
        if "home" not in byside or "away" not in byside: return {}
        def val(side, keys):
            for st in byside[side].get("statistics", []):
                nm = re.sub(r"[^a-z]", "", str(st.get("name", "")).lower())
                if any(k in nm for k in keys):
                    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(st.get("displayValue", st.get("value", ""))))
                    return float(m.group(1)) if m else None
            return None
        for key, toks in KEYMAP:
            h, a = val("home", toks), val("away", toks)
            if h is not None and a is not None: res[key] = (h, a)
    except Exception: return {}
    return res

def enrich(slug, hist, ref):
    """يملأ الإحصاءات الناقصة (خصوصًا الركنيات) لمباريات PitchAPI المنتهية من ESPN. يرجع عدد المباريات المُكمَّلة."""
    games = [h for h in hist if len(h) > 5 and (ref - h[0]).days <= xgm.WINDOW_DAYS and "corners" not in xgm.STATS.get(h[5], {})]
    if not games: return 0
    evs = scoreboard(slug, min(g[0] for g in games) - timedelta(days=1), min(max(g[0] for g in games) + timedelta(days=1), ref))
    if not evs: return 0
    byteams = {}
    for e in evs: byteams.setdefault((norm(e["home"]), norm(e["away"])), []).append(e)
    todo = []
    for d, h, a, gh, ga, mid in games:
        hn, an = norm(last50.NAMES.get(h, "")), norm(last50.NAMES.get(a, ""))
        cand = byteams.get((hn, an))
        if not cand:   # مطابقة تقريبية صارمة
            best = [(difflib.SequenceMatcher(None, hn, k[0]).ratio() * difflib.SequenceMatcher(None, an, k[1]).ratio(), k) for k in byteams]
            sc, k = max(best, default=(0, None), key=lambda x: x[0])
            cand = byteams[k] if k and sc >= 0.72 else None
        ev = min(cand, key=lambda e: abs((e["date"] - d).days)) if cand else None
        if ev and abs((ev["date"] - d).days) <= 1: todo.append((mid, ev["id"]))
    def one(t):
        st = summary_for(slug, t[1])
        if st:
            cur = xgm.STATS.setdefault(t[0], {})
            for k, v in st.items(): cur.setdefault(k, v)   # لا يستبدل ما جاء من PitchAPI
            return 1
        return 0
    with ThreadPoolExecutor(4) as ex: return sum(ex.map(one, todo))


# ---- حقائق المباراة القادمة من ESPN: الملعب، الشكل، أسعار شركة الرهان (مجانية بلا مفتاح؛ شكل الرد مؤكد من scoreboard الحقيقي) ----
def _dec(a):
    """سعر أمريكي (+215 / -145) -> عشري."""
    try: v = float(str(a).replace("+", "").strip())
    except (TypeError, ValueError): return None
    if v == 0: return None
    return round(1 + v / 100, 3) if v > 0 else round(1 + 100 / abs(v), 3)

def _norm_probs(decs):
    """احتمالات السوق بعد إزالة هامش الشركة (تطبيع نسبي) + حجم الهامش."""
    if not decs or any(d is None or d <= 1 for d in decs): return None, None
    imp = [1 / d for d in decs]; s = sum(imp)
    return [round(100 * i / s, 1) for i in imp], round(100 * (s - 1), 1)

def day_events(slug, d0, d1):
    return (_get(f"{BASE}/{slug}/scoreboard?dates={d0:%Y%m%d}-{d1:%Y%m%d}&limit=300", ttl=600) or {}).get("events", [])

def find_event(slug, day, hname, aname):
    hn, an, best = norm(hname), norm(aname), None
    for e in day_events(slug, day - timedelta(days=1), day + timedelta(days=1)):
        try:
            tm = {x["homeAway"]: x["team"].get("displayName") or x["team"].get("name") for x in e["competitions"][0]["competitors"]}
            sc = difflib.SequenceMatcher(None, hn, norm(tm["home"])).ratio() * difflib.SequenceMatcher(None, an, norm(tm["away"])).ratio()
        except Exception: continue
        if sc >= 0.72 and (best is None or sc > best[0]): best = (sc, e)
    return best[1] if best else None

def facts(e):
    c = e["competitions"][0]; v = c.get("venue") or {}; comp = {x["homeAway"]: x for x in c["competitors"]}
    out = {"event_id": e.get("id"), "state": (c.get("status") or {}).get("type", {}).get("state"),
           "venue": {"name": v.get("fullName") or (e.get("venue") or {}).get("displayName"), "city": (v.get("address") or {}).get("city"), "country": (v.get("address") or {}).get("country")},
           "form": {s: comp[s].get("form") for s in comp}, "records": {s: ((comp[s].get("records") or [{}])[0]).get("summary") for s in comp}, "market": None}
    o = (c.get("odds") or [None])[0]
    if not o: return out
    ml, tot = o.get("moneyline") or {}, o.get("total") or {}
    side = lambda k, w: _dec((((ml.get(k) or {}).get(w)) or {}).get("odds"))
    m = {"provider": (o.get("provider") or {}).get("displayName") or (o.get("provider") or {}).get("name")}
    for w in ("close", "open"):
        decs = [side(k, w) for k in ("home", "draw", "away")]; pr, ov = _norm_probs(decs)
        if pr: m["ml" if w == "close" else "ml_open"] = {"dec": dict(zip(("home", "draw", "away"), decs)), "prob": dict(zip(("home", "draw", "away"), pr)), "overround": ov}
    def tside(k, w):
        x = ((tot.get(k) or {}).get(w)) or {}; ln = re.sub(r"[^\d.]", "", str(x.get("line", "")))
        return (float(ln) if ln else None), _dec(x.get("odds"))
    for w in ("close", "open"):
        (lo, do), (lu, du) = tside("over", w), tside("under", w); pr, ov = _norm_probs([do, du])
        if pr and lo and lo == lu: m["total" if w == "close" else "total_open"] = {"line": lo, "over": {"dec": do, "prob": pr[0]}, "under": {"dec": du, "prob": pr[1]}, "overround": ov}
    out["market"] = m if ("ml" in m or "total" in m) else None
    return out
