from app import last50

def test_picks_rank_and_threshold():
    comb = {"goals": {"lines": [{"line": 1.5, "home": 7, "home_n": 7, "away": 6, "away_n": 7, "avg_pct": 93},
                                {"line": 3.5, "home": 1, "home_n": 7, "away": 2, "away_n": 7, "avg_pct": 21}]}}
    out = last50._picks(comb, {}, {}, {}, 7)
    assert out[0]["text"] == "المجموع أكثر من 1.5" and out[0]["rate"] == 93 and out[0]["hits"] == 13 and out[0]["of"] == 14
    assert any(x["text"] == "المجموع أقل من 3.5" and x["rate"] == 79 for x in out)
    assert all(x["rate"] >= 75 for x in out)
