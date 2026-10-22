import os, time, threading, requests

BASE = "https://api.pitchapi.dev"

class PitchError(Exception):
    def __init__(self, code, message, status=502):
        super().__init__(message); self.code, self.status = code, status

_cache, _lock = {}, threading.Lock()
_sem = threading.Semaphore(4)  # حد تزامن متحفظ (سياسة الاستخدام العادل)

def get(path, params=None, ttl=300):
    key = os.environ.get("PITCHAPI_KEY", "").strip()
    if not key:
        raise PitchError("NO_KEY", "متغير البيئة PITCHAPI_KEY غير مضبوط", 503)
    ck = (path, tuple(sorted((params or {}).items())))
    with _lock:
        hit = _cache.get(ck)
        if hit and hit[0] > time.time():
            return hit[1]
    for attempt in range(3):
        try:
            with _sem:
                r = requests.get(BASE + path, params=params, headers={"X-API-KEY": key}, timeout=15)
        except requests.RequestException:
            if attempt == 2: raise PitchError("TIMEOUT", "تعذر الاتصال بمصدر البيانات", 504)
            time.sleep(1 + attempt); continue
        if r.status_code == 429 and attempt < 2:
            time.sleep(min(int(r.headers.get("Retry-After", "2") or 2), 10)); continue
        if r.status_code == 401:
            raise PitchError("UNAUTHORIZED", "المفتاح مفقود أو غير صالح", 502)
        if r.status_code >= 500 and attempt < 2:
            time.sleep(1 + attempt); continue
        try: body = r.json()
        except ValueError: raise PitchError("BAD_RESPONSE", "استجابة غير متوقعة", 502)
        if "error" in body:
            e = body["error"]; raise PitchError(e.get("code", "ERROR"), e.get("message", ""), r.status_code)
        data = body.get("data")
        with _lock: _cache[ck] = (time.time() + ttl, data)
        return data
    raise PitchError("RATE_LIMIT_EXCEEDED", "تجاوز حد الاستخدام، أعد المحاولة لاحقًا", 429)
