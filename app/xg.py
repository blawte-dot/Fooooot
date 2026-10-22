"""مخزن xG لكل مباراة (من إحصاءات المباراة) يُملأ في الخلفية. لا xG = يبقى النموذج على الأهداف الفعلية."""
import threading
from concurrent.futures import ThreadPoolExecutor
from . import pitch_client as pc, last50

STORE, STATS, MISSING, _state = {}, {}, set(), {}   # STATS: كل إحصاءات المباراة المفهومة (xg, sot, shots, yellow, corners, fouls)
WINDOW_DAYS, BLOCK = 800, False
SLUG = {}   # league_id -> ESPN slug (يُضبط من الخدمة للدوريات المعروفة)

def coverage(hist, ref):
    ids = [h[5] for h in hist if len(h) > 5 and (ref - h[0]).days <= WINDOW_DAYS]
    return sum(1 for i in ids if i in STORE) / len(ids) if ids else 0.0

def state(lg): return _state.get(lg, {"running": False, "total": 0, "done": 0})

_espn_done = {}
def st_done(lg): return _espn_done.get(lg, 0) > __import__('time').time()

def ensure(lg, hist, ref):
    st = _state.get(lg)
    if st and st["running"]: return st
    ids = [(h[5], h[1], h[2]) for h in sorted((h for h in hist if len(h) > 5 and (ref - h[0]).days <= WINDOW_DAYS and h[5] not in STATS and h[5] not in MISSING), key=lambda h: h[0], reverse=True)]   # الأحدث أولًا: تجهز أول 120 مباراة بسرعة فتظهر الأسواق مبكرًا
    st = {"running": bool(ids), "total": len(ids), "done": 0}; _state[lg] = st
    if not ids and not (lg in SLUG and any(len(h) > 5 and "corners" not in STATS.get(h[5], {}) and h[5] in STATS and not st_done(lg) for h in hist)):
        return st
    st["running"] = True
    def one(t):
        i, hid, aid = t
        try:
            d = last50._match_stats(i, hid, aid)
            if d:
                STATS[i] = d
                if "xg" in d: STORE[i] = d["xg"]
            else: MISSING.add(i)
        except Exception: MISSING.add(i)
        st["done"] += 1
    def work():
        try:
            with ThreadPoolExecutor(6) as ex: list(ex.map(one, ids))
            if lg in SLUG:
                try:
                    from . import espn
                    st["espn_filled"] = espn.enrich(SLUG[lg], hist, ref)
                    _espn_done[lg] = __import__("time").time() + 6 * 3600
                except Exception: pass
        finally: st["running"] = False
    if BLOCK: work()
    else: threading.Thread(target=work, daemon=True).start()
    return st
