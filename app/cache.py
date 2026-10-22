"""ذاكرة مؤقتة صغيرة بمدة صلاحية وحد أقصى، مع منع الحساب المزدوج لنفس المفتاح (single-flight). الأخطاء لا تُخزَّن أبدًا."""
import threading, time

class TTLCache:
    def __init__(self, maxsize=200): self.d, self.locks, self.max, self.mu = {}, {}, maxsize, threading.Lock()

    def get_or(self, key, ttl, fn):
        with self.mu:
            hit = self.d.get(key)
            if hit and hit[0] > time.time(): return hit[1]
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:                       # من وصل ثانيًا ينتظر نتيجة الأول بدل أن يحسب من جديد
            with self.mu:
                hit = self.d.get(key)
                if hit and hit[0] > time.time(): return hit[1]
            val = fn()
            with self.mu:
                self.d[key] = (time.time() + (ttl(val) if callable(ttl) else ttl), val)
                if len(self.d) > self.max:
                    for k in sorted(self.d, key=lambda x: self.d[x][0])[:len(self.d) - self.max]: self.d.pop(k, None); self.locks.pop(k, None)
            return val

    def clear(self):
        with self.mu: self.d.clear(); self.locks.clear()

    def put(self, key, val, ttl):
        with self.mu: self.d[key] = (time.time() + ttl, val)
