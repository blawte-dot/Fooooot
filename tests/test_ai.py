import json
import pytest
from app import ai, service

P = {"match": {"id": "m_AAAAAA", "league": "Premier League", "home": "Arsenal", "away": "Chelsea"}, "horizon_days": 2, "n_matches": 900, "not_used": ["Elo"],
     "prediction": {"home": 50, "draw": 27, "away": 23, "xg_home": 1.8, "xg_away": 1.1}, "confidence": {"level": "متوسطة", "reasons": ["بيانات قليلة"]},
     "scope": {"home_n": 80, "away_n": 75}, "features": {"home": {"overall": {"form": ["W", "W", "D", "L", "W"], "gf": 1.9, "ga": 0.8, "over25": 60, "btts": 50, "n": 10}}, "away": {"overall": None},
                                                         "h2h": {"record": {"home_wins": 3, "draws": 2, "away_wins": 1}}}}
B = {"events": [{"group": "goals", "label": "المجموع أكثر من 2.5", "p": 64.2, "base": 52.1, "grade": "جيد", "call": "نعم"},
                {"group": "corners", "label": "المجموع أكثر من 9.5", "p": 71.0, "base": 55.0, "grade": "ضعيف", "call": "لا"}],
     "groups": [{"key": "corners_2h", "available": False, "reason": "إحصاءات الركنيات - الشوط الثاني غير كافية"}]}
OUT = {"summary": "المضيف أقوى هجوميًا (1.8 هدف متوقع مقابل 1.1).", "model_view_ok": True, "model_view_note": "", "key_factors": [{"factor": "متوسط أهدافه 1.9 في آخر 10", "direction": "home", "weight": "high"}],
       "risks": ["احتمال 75% قد يكون مبالغًا فيه"], "data_gaps": [],
       "best_supported_markets": [{"label": "المجموع أكثر من 2.5", "reason": "احتمال 90% مقابل المعتاد 52.1%"}, {"label": "سوق من خيال النموذج", "reason": "x"}],
       "avoid_markets": [{"label": "المجموع أكثر من 9.5", "reason": "سوق ضعيف"}], "confidence": "medium", "confidence_reason": "بيانات كافية"}
GOOD = lambda out: (200, {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(out, ensure_ascii=False) + "\n```"}]}}]})

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SECRETKEY123"); monkeypatch.setattr(service, "predict_match", lambda mid: P); monkeypatch.setattr(service, "peek_board", lambda mid: B)
    monkeypatch.setattr(ai, "PER_IP", 8); monkeypatch.setattr(ai, "GLOBAL", 40); monkeypatch.setattr(ai.time, "sleep", lambda s: None)

def test_analysis_numbers_come_from_engine_and_lies_are_flagged(monkeypatch):
    calls = []; monkeypatch.setattr(ai, "_post", lambda model, body, key: (calls.append((model, body, key)), GOOD(OUT))[1])
    r = ai.analyze("m_AAAAAA", "1.1.1.1"); a = r["analysis"]
    assert [x["label"] for x in a["best_supported_markets"]] == ["المجموع أكثر من 2.5"]                      # الحدث الوهمي حُذف
    assert a["best_supported_markets"][0]["p"] == 64.2 and a["best_supported_markets"][0]["grade"] == "جيد"    # الرقم من المحرك لا من Gemini
    assert a["avoid_markets"][0]["call"] == "لا"
    u = a["unverified_numbers"]; assert "75%" in u and "90%" in u                                              # أرقام بلا أصل في البيانات
    assert not {"1.8", "1.1", "1.9", "52.1%", "10"} & set(u)                                                   # أرقام البيانات سليمة
    assert "SECRETKEY123" not in json.dumps(r, ensure_ascii=False)
    body = calls[0][1]; ctx = json.loads(body["contents"][0]["parts"][0]["text"])
    assert ctx["model"]["home"] == 50 and ctx["events"][0]["label"] == "المجموع أكثر من 2.5" and calls[0][2] == "SECRETKEY123" and "responseSchema" in body["generationConfig"]
    assert ai.analyze("m_AAAAAA", "1.1.1.1") is r and len(calls) == 1                                           # التكرار من الذاكرة بلا استدعاء

def test_400_schema_fallback_and_bad_json(monkeypatch):
    seq = [(400, {}), GOOD(OUT)]; bodies = []
    monkeypatch.setattr(ai, "_post", lambda m, b, k: (bodies.append(b), seq.pop(0))[1])
    assert ai.analyze("m_AAAAAA", "2.2.2.2")["analysis"]["summary"].startswith("المضيف")
    assert "responseSchema" in bodies[0]["generationConfig"] and "responseSchema" not in bodies[1]["generationConfig"]    # أعاد المحاولة بلا مخطط
    monkeypatch.setattr(ai, "_post", lambda m, b, k: (200, {"candidates": [{"content": {"parts": [{"text": "ليس JSON إطلاقًا"}]}}]}))
    with pytest.raises(ai.AIError) as e: ai.analyze("m_BBBBBB", "2.2.2.2")
    assert e.value.code == "AI_BAD_JSON"
    monkeypatch.setattr(ai, "_post", lambda m, b, k: (200, {"candidates": [{"content": {"parts": [{"text": "هذا هو الرد: " + json.dumps(OUT, ensure_ascii=False) + " انتهى"}]}}]}))
    assert ai.analyze("m_CCCCCC", "2.2.2.2")["analysis"]["confidence"] == "medium"                              # يلتقط JSON من وسط نص

def test_errors_never_leak_key_and_limits(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(ai.AIError) as e: ai.analyze("m_AAAAAA", "3.3.3.3")
    assert e.value.code == "NO_AI_KEY" and e.value.status == 503
    monkeypatch.setenv("GEMINI_API_KEY", "SECRETKEY123")
    monkeypatch.setattr(ai, "_post", lambda m, b, k: (401, {"error": {"message": "API key SECRETKEY123 invalid"}}))
    with pytest.raises(ai.AIError) as e: ai.analyze("m_AAAAAA", "3.3.3.3")
    assert e.value.code == "AI_AUTH" and "SECRETKEY123" not in str(e.value)
    monkeypatch.setattr(ai, "_post", lambda m, b, k: (429, {})); 
    with pytest.raises(ai.AIError) as e: ai.analyze("m_AAAAAA", "3.3.3.3")
    assert e.value.code == "AI_RATE_LIMIT" and e.value.status == 429
    ai.reset(); monkeypatch.setattr(ai, "PER_IP", 2); monkeypatch.setattr(ai, "_post", lambda m, b, k: GOOD(OUT))
    ai.analyze("m_AAAAAA", "4.4.4.4"); ai.analyze("m_AAAAAA", "4.4.4.4"); ai.analyze("m_BBBBBB", "4.4.4.4")       # المكرر من الذاكرة لا يُحسب
    with pytest.raises(ai.AIError) as e: ai.analyze("m_CCCCCC", "4.4.4.4")
    assert e.value.code == "AI_LIMIT" and ai.analyze("m_DDDDDD", "5.5.5.5")                                       # IP آخر يعمل
    ai.reset(); monkeypatch.setattr(ai, "PER_IP", 99); monkeypatch.setattr(ai, "GLOBAL", 1); ai.analyze("m_AAAAAA", "6.6.6.6")
    with pytest.raises(ai.AIError): ai.analyze("m_BBBBBB", "7.7.7.7")                                              # سقف عام لحماية الحصة

def test_news_sources_and_endpoints(monkeypatch):
    resp = (200, {"candidates": [{"content": {"parts": [{"text": "- غياب لاعب س (12 أكتوبر)\\n" + "ب" * 3000}]},
                                  "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://example.com/a", "title": "خبر"}}, {"web": {"uri": "http://evil.example/x", "title": "x"}},
                                                                            {"web": {"uri": "javascript:alert(1)", "title": "y"}}]}}]})
    seen = []; monkeypatch.setattr(ai, "_post", lambda m, b, k: (seen.append(b), resp)[1])
    r = ai.news("m_AAAAAA", "8.8.8.8")
    assert [s["uri"] for s in r["sources"]] == ["https://example.com/a"] and len(r["text"]) <= 1500 and seen[0]["tools"] == [{"google_search": {}}]    # https فقط
    assert "الغياب" not in r["note"] and "لم تدخل في أي احتمال" in r["note"]
    from app.main import app
    c = app.test_client(); monkeypatch.setattr(ai, "_post", lambda m, b, k: GOOD(OUT))
    ok = c.get("/api/ai/m_AAAAAA", headers={"X-Forwarded-For": "9.9.9.9"}); assert ok.status_code == 200 and ok.json["analysis"]["confidence"] == "medium"
    assert c.get("/api/ai/bad").status_code == 400 and c.get("/api/ai-news/bad").status_code == 400
    monkeypatch.delenv("GEMINI_API_KEY"); ai.reset(); e = c.get("/api/ai/m_ZZZZZZ"); assert e.status_code == 503 and e.json["error"]["code"] == "NO_AI_KEY"

DOSS = {"summary": "مباراة مهمة وترجيح المحرك للمضيف 50%.", "venue": {"notes": "ملعب سعته 40 ألفًا", "factors": ["عشب طبيعي"]}, "rivalry": {"is_derby": True, "notes": "ديربي"},
        "teams": {"home": {"manager": "س", "style": "ضغط عالٍ", "strengths": ["هجوم"], "weaknesses": ["كرات ثابتة"]}, "away": {"manager": "ص", "style": "دفاع", "strengths": [], "weaknesses": []}},
        "availability": {"home": ["غياب لاعب"], "away": []}, "schedule_and_motivation": "يلعب بعد 3 أيام", "referee": "غير معروف", "advice": ["راقب المجموع أكثر من 2.5 (64.2%) وتجنب احتمال 88% المختلق"], "unknowns": ["التشكيل"], "confidence": "medium"}

def test_dossier_job_sources_and_guards(monkeypatch):
    import time
    monkeypatch.setattr(service, "market_facts", lambda mid: {"venue": {"name": "Hill Dickinson Stadium", "city": "Liverpool"}, "form": {"home": "DWWDD", "away": "DLDWL"}, "records": {}, "market": {"ml": {"prob": {"home": 30.3, "draw": 25.4, "away": 44.3}}}})
    seen = []
    resp = (200, {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(DOSS, ensure_ascii=False) + "\n```"}]},
                                  "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://example.com/s", "title": "مصدر"}}, {"web": {"uri": "http://bad.example", "title": "x"}}]}}]})
    monkeypatch.setattr(ai, "_post", lambda m, b, k: (seen.append(b), resp)[1])
    r = ai.dossier("m_AAAAAA", "1.2.3.4"); assert r["state"] == "running"
    for _ in range(60):
        r = ai.dossier("m_AAAAAA", "1.2.3.4")
        if r["state"] != "running": break
        time.sleep(0.05)
    assert r["state"] == "done" and r["structured"] and [s["uri"] for s in r["sources"]] == ["https://example.com/s"]
    d = r["dossier"]; assert d["rivalry"]["is_derby"] is True and d["venue"]["notes"].startswith("ملعب") and d["teams"]["home"]["style"] == "ضغط عالٍ"
    assert d["unverified_percentages"] == ["88%"] and "64.2%" not in d["unverified_percentages"]                 # الأرقام الجغرافية/الإحصائية من البحث لا تُعلَّم، النسب المؤلَّفة تُعلَّم
    body = seen[0]; ctx = json.loads(body["contents"][0]["parts"][0]["text"])
    assert body["tools"] == [{"google_search": {}}] and ctx["espn"]["venue"]["name"] == "Hill Dickinson Stadium" and ctx["espn"]["market_prob"]["home"] == 30.3
    assert "SECRETKEY123" not in json.dumps(r, ensure_ascii=False) and ai.dossier("m_AAAAAA", "9.9.9.9")["state"] == "done" and len(seen) == 1       # التكرار من الذاكرة

def test_dossier_unstructured_fallback_errors_and_limits(monkeypatch):
    import time
    monkeypatch.setattr(service, "market_facts", lambda mid: None)
    monkeypatch.setattr(ai, "_post", lambda m, b, k: (200, {"candidates": [{"content": {"parts": [{"text": "نص حر بلا JSON: الملعب كبير."}]}}]}))
    ai.dossier("m_BBBBBB", "2.2.2.2"); time.sleep(0.3); r = ai.dossier("m_BBBBBB", "2.2.2.2")
    assert r["state"] == "done" and r["structured"] is False and "الملعب كبير" in r["raw"]
    monkeypatch.setattr(ai, "_post", lambda m, b, k: (401, {})); ai.dossier("m_CCCCCC", "3.3.3.3"); time.sleep(0.3)
    e = ai.dossier("m_CCCCCC", "3.3.3.3"); assert e["state"] == "error" and e["code"] == "AI_AUTH"
    ai.reset(); monkeypatch.setattr(ai, "PER_IP", 1); monkeypatch.setattr(ai, "_post", lambda m, b, k: (200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}))
    ai.dossier("m_DDDDDD", "4.4.4.4")
    with pytest.raises(ai.AIError) as x: ai.dossier("m_EEEEEE", "4.4.4.4")
    assert x.value.code == "AI_LIMIT"
    from app.main import app
    assert app.test_client().get("/api/ai-dossier/bad").status_code == 400
