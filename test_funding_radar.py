import datetime as dt
import funding_radar as fr

def test_parse_and_rank():
    text = '''Here you go
```json
[{"company":"A","amount":"$1M","stage":"Seed","date":"2026-08","investors":["X Ventures","Y"],"lead_investors":["X Ventures"],"sources":["https://a.com"],"similarity":0.9},
 {"company":"B","stage":"Series B","date":"2025-01","investors":["Y"],"lead_investors":[],"sources":[],"similarity":0.4}]
```'''
    deals = fr.parse_deals(text)
    assert [d.company for d in deals] == ["A", "B"]
    deals[0].verified_investors = ["X Ventures"]
    m = fr.rank(deals, dt.date(2026, 11, 20), 18)
    assert m[0].name == "X Ventures" and m[0].led == 1 and m[0].verified == 1
    assert {x.name for x in m} == {"X Ventures", "Y"}

def test_recency_and_norm():
    t = dt.date(2026, 11, 20)
    assert fr._recency("2026-11", t, 18) == 1.0
    assert fr._recency("2024-01", t, 18) == 0.2
    assert fr._norm("Accel Partners") == fr._norm("accel")

def test_collect_results():
    r = fr._collect_results({"results": [{"url": "https://x", "title": "t"}, {"nested": {"url": "https://y"}}]})
    assert [x["url"] for x in r] == ["https://x", "https://y"]
