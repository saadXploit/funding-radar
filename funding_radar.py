#!/usr/bin/env python3
"""
Funding Radar: who would actually fund my startup?

Give it a one-line startup description. It uses TinyFish to:
  1. find similar startups that raised money in the last 12-18 months (Research API),
  2. widen the net with live news search (Search API),
  3. verify every deal against the live source page (Fetch API),
  4. rank the investors behind those deals, with proof links for every match.

Standard library only. Needs:  export TINYFISH_API_KEY=...
Usage:
  python funding_radar.py "AI bookkeeping assistant for small shops in Africa"
  python funding_radar.py "..." --months 18 --top 15 --out report
  python funding_radar.py --demo            # offline demo with sample data
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field

RESEARCH_URL = "https://agent.tinyfish.ai/v1/automation/run-research"
SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"

STAGE_WEIGHT = {"pre-seed": 1.2, "seed": 1.2, "series a": 1.0, "series b": 0.8,
                "series c": 0.6, "growth": 0.5, "grant": 0.7, "unknown": 0.9}


# ---------------------------------------------------------------- data model
@dataclass
class Deal:
    company: str
    description: str
    amount: str
    stage: str
    date: str                     # YYYY-MM or YYYY-MM-DD if known
    investors: list[str]
    lead_investors: list[str]
    sources: list[str]
    similarity: float = 0.5       # 0..1, how close to the user's startup
    verified_investors: list[str] = field(default_factory=list)


@dataclass
class InvestorMatch:
    name: str
    score: float
    deals: list[dict]
    led: int
    verified: int


# ---------------------------------------------------------------- HTTP helpers
def _key() -> str:
    k = os.environ.get("TINYFISH_API_KEY", "").strip()
    if not k:
        sys.exit("Set TINYFISH_API_KEY first (get one at agent.tinyfish.ai/api-keys).")
    return k


def _request(method: str, url: str, body: dict | None = None, timeout: int = 150):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-API-Key": _key(), "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    return urllib.request.urlopen(req, timeout=timeout)


def research(query: str, timeout: int = 2700) -> str:
    """Run a TinyFish deep research job and return the synthesized text."""
    body = {"query": query, "mode": "deep", "stream": True}
    parts, final = [], None
    with _request("POST", RESEARCH_URL, body, timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "synthesis_delta":
                parts.append(ev.get("delta", ""))
            elif ev.get("event") == "done":
                final = ev.get("final_result")
                break
    if isinstance(final, dict):
        final = final.get("result") or final.get("report") or json.dumps(final)
    return final if isinstance(final, str) and final.strip() else "".join(parts)


def search(query: str, after_date: str | None = None, domain_type: str = "news") -> list[dict]:
    params = {"query": query, "location": "US", "language": "en", "domain_type": domain_type}
    if after_date:
        params["after_date"] = after_date
    try:
        with _request("GET", SEARCH_URL + "?" + urllib.parse.urlencode(params), None, 15) as r:
            payload = json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  search failed: {e}", file=sys.stderr)
        return []
    return _collect_results(payload)


def fetch_pages(urls: list[str]) -> dict[str, str]:
    """Return {url: page text} using TinyFish Fetch (live, ttl=0 off by default to save credits)."""
    out: dict[str, str] = {}
    for i in range(0, len(urls), 10):
        chunk = urls[i:i + 10]
        try:
            with _request("POST", FETCH_URL, {"urls": chunk}, 150) as r:
                payload = json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"  fetch failed for {len(chunk)} urls: {e}", file=sys.stderr)
            continue
        items = payload.get("results") or payload.get("data") or payload
        if isinstance(items, dict):
            items = [dict(v, url=k) if isinstance(v, dict) else {"url": k, "content": v}
                     for k, v in items.items()]
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict):
                u = it.get("url") or it.get("source_url") or ""
                text = it.get("markdown") or it.get("content") or it.get("text") or json.dumps(it)
                if u:
                    out[u] = str(text)
        # Fall back: if the response did not key by URL, attach it to every URL in the chunk
        if not any(u in out for u in chunk):
            blob = json.dumps(payload)
            for u in chunk:
                out[u] = blob
    return out


def _collect_results(obj) -> list[dict]:
    """Walk an unknown JSON shape and pull out {title, url, snippet} items."""
    found = []
    if isinstance(obj, dict):
        if "url" in obj and isinstance(obj["url"], str):
            found.append({"url": obj["url"],
                          "title": obj.get("title", ""),
                          "snippet": obj.get("snippet") or obj.get("description") or ""})
        for v in obj.values():
            found += _collect_results(v)
    elif isinstance(obj, list):
        for v in obj:
            found += _collect_results(v)
    return found


# ---------------------------------------------------------------- pipeline
def build_query(description: str, months: int, today: dt.date) -> str:
    since = (today - dt.timedelta(days=months * 30)).strftime("%B %Y")
    return f"""Find startups similar to this one: "{description}".
Only include companies that announced a funding round (pre-seed, seed, Series A/B, or grant)
between {since} and {today:%B %Y}. Aim for 15-30 companies. Prefer the closest matches by
product, customer and geography; include global examples, not just the US.

For every company give the round date, amount, stage, ALL named investors (mark the lead),
and at least one source URL (press release, TechCrunch, Crunchbase news, the company blog, etc.)
that names the investors.

Return ONLY a JSON array inside a ```json code block, one object per company:
{{"company": str, "description": str, "amount": str, "stage": str, "date": "YYYY-MM",
  "investors": [str], "lead_investors": [str], "sources": [url], "similarity": 0.0-1.0}}"""


def parse_deals(text: str) -> list[Deal]:
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S) or re.search(r"(\[\s*\{.*\}\s*\])", text, re.S)
    if not m:
        return []
    try:
        rows = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    deals = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("company"):
            continue
        invs = [s.strip() for s in r.get("investors") or [] if isinstance(s, str) and s.strip()]
        leads = [s.strip() for s in r.get("lead_investors") or [] if isinstance(s, str) and s.strip()]
        deals.append(Deal(
            company=str(r["company"]).strip(),
            description=str(r.get("description", "")),
            amount=str(r.get("amount", "") or "undisclosed"),
            stage=str(r.get("stage", "") or "unknown"),
            date=str(r.get("date", "") or ""),
            investors=sorted(set(invs + leads), key=str.lower),
            lead_investors=leads,
            sources=[u for u in r.get("sources") or [] if isinstance(u, str) and u.startswith("http")],
            similarity=_clamp(r.get("similarity", 0.5)),
        ))
    return deals


def enrich_with_search(deals: list[Deal], after_date: str) -> None:
    """Find a news source for deals the research step left without one."""
    for d in deals:
        if d.sources:
            continue
        hits = search(f'"{d.company}" raises funding', after_date=after_date)
        d.sources = [h["url"] for h in hits[:2]]


def verify(deals: list[Deal]) -> None:
    """Mark an investor as verified when its name appears on a live source page next to the company."""
    urls = sorted({u for d in deals for u in d.sources})
    pages = fetch_pages(urls) if urls else {}
    for d in deals:
        text = " ".join(pages.get(u, "") for u in d.sources).lower()
        if d.company.lower().split()[0] not in text:
            continue
        d.verified_investors = [i for i in d.investors if _name_in(i, text)]


def rank(deals: list[Deal], today: dt.date, months: int) -> list[InvestorMatch]:
    by_inv: dict[str, dict] = defaultdict(lambda: {"score": 0.0, "deals": [], "led": 0, "verified": 0, "name": ""})
    for d in deals:
        rec = _recency(d.date, today, months)
        stage = STAGE_WEIGHT.get(_stage_key(d.stage), 0.9)
        for inv in d.investors:
            key = _norm(inv)
            b = by_inv[key]
            b["name"] = b["name"] or inv
            lead = inv in d.lead_investors
            ver = inv in d.verified_investors
            # Similar company + recent + early stage + led it + verified on a live page.
            pts = (0.4 + d.similarity) * rec * stage * (1.5 if lead else 1.0) * (1.25 if ver else 0.8)
            b["score"] += pts
            b["led"] += lead
            b["verified"] += ver
            b["deals"].append({"company": d.company, "date": d.date, "stage": d.stage,
                               "amount": d.amount, "lead": lead, "verified": ver,
                               "sources": d.sources})
    out = [InvestorMatch(name=b["name"], score=round(b["score"], 2), deals=b["deals"],
                         led=b["led"], verified=b["verified"]) for b in by_inv.values()]
    return sorted(out, key=lambda m: (-m.score, -m.verified, m.name.lower()))


# ---------------------------------------------------------------- helpers
def _clamp(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.5


def _norm(name: str) -> str:
    n = name.lower().replace("&", "and")
    n = re.sub(r"\b(ventures?|capital|partners|vc|fund|llc|inc|ltd|management)\b", "", n)
    return re.sub(r"[^a-z0-9]+", "", n) or name.lower()


def _name_in(name: str, text: str) -> bool:
    core = re.sub(r"\b(ventures?|capital|partners|vc|fund|llc|inc|ltd)\b", "", name, flags=re.I).strip()
    return bool(core) and core.lower() in text


def _stage_key(stage: str) -> str:
    s = stage.lower()
    for k in STAGE_WEIGHT:
        if k in s:
            return k
    return "unknown"


def _recency(date: str, today: dt.date, months: int) -> float:
    m = re.match(r"(\d{4})-(\d{1,2})", date or "")
    if not m:
        return 0.6
    age = (today.year - int(m.group(1))) * 12 + today.month - int(m.group(2))
    if age < 0:
        return 0.6
    if age > months:
        return 0.2
    return 1.0 - 0.5 * age / max(months, 1)   # 1.0 this month -> 0.5 at the window edge


# ---------------------------------------------------------------- output
def to_markdown(desc: str, matches: list[InvestorMatch], deals: list[Deal], top: int) -> str:
    lines = [f"# Funding Radar\n", f"**Startup:** {desc}\n",
             f"Found {len(deals)} similar funded companies and {len(matches)} investors. "
             f"Top {min(top, len(matches))} below.\n"]
    for i, m in enumerate(matches[:top], 1):
        lines.append(f"## {i}. {m.name} (score {m.score})")
        lines.append(f"{len(m.deals)} similar deal(s), led {m.led}, verified on live page {m.verified}\n")
        for d in m.deals:
            tag = " (lead)" if d["lead"] else ""
            ok = "verified" if d["verified"] else "unverified"
            src = ", ".join(f"[source]({u})" for u in d["sources"][:2]) or "no source"
            lines.append(f"- **{d['company']}**{tag}: {d['stage']}, {d['amount']}, {d['date']} ({ok}) {src}")
        lines.append("")
    return "\n".join(lines)


def to_html(desc: str, matches: list[InvestorMatch], deals: list[Deal], top: int) -> str:
    e = html.escape
    cards = []
    for i, m in enumerate(matches[:top], 1):
        rows = "".join(
            f"<li><b>{e(d['company'])}</b>{' <span class=lead>lead</span>' if d['lead'] else ''} "
            f"· {e(d['stage'])} · {e(d['amount'])} · {e(d['date'])} "
            f"<span class={'ok' if d['verified'] else 'no'}>{'verified' if d['verified'] else 'unverified'}</span> "
            + " ".join(f"<a href='{e(u)}' target=_blank>source</a>" for u in d["sources"][:2]) + "</li>"
            for d in m.deals)
        cards.append(f"<div class=card><div class=hd><span class=rk>{i}</span><h2>{e(m.name)}</h2>"
                     f"<span class=sc>{m.score}</span></div><p class=meta>{len(m.deals)} similar deals · "
                     f"led {m.led} · verified {m.verified}</p><ul>{rows}</ul></div>")
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Funding Radar</title>
<meta name=viewport content="width=device-width,initial-scale=1"><style>
body{{font:15px/1.5 system-ui,sans-serif;background:#0b0e14;color:#e8ebf0;max-width:860px;margin:40px auto;padding:0 16px}}
h1{{margin:0}}.sub{{color:#9aa3b2}}.card{{background:#141923;border:1px solid #232a36;border-radius:12px;padding:16px 18px;margin:14px 0}}
.hd{{display:flex;align-items:center;gap:12px}}.hd h2{{font-size:18px;margin:0;flex:1}}.rk{{color:#facc15;font-weight:700}}
.sc{{background:#facc15;color:#111;border-radius:999px;padding:2px 10px;font-weight:700}}.meta{{color:#9aa3b2;margin:4px 0 8px}}
ul{{margin:0;padding-left:18px}}a{{color:#5eead4}}.ok{{color:#4ade80}}.no{{color:#9aa3b2}}.lead{{color:#facc15}}
</style></head><body><h1>Funding Radar</h1><p class=sub>{e(desc)} · {len(deals)} similar funded companies · {len(matches)} investors</p>
{''.join(cards)}<p class=sub>Built with TinyFish (Research, Search, Fetch). Scores weight similarity, recency, stage, lead role and live verification.</p></body></html>"""


# ---------------------------------------------------------------- main
def run(description: str, months: int = 18, top: int = 15, do_verify: bool = True,
        today: dt.date | None = None) -> tuple[list[InvestorMatch], list[Deal]]:
    today = today or dt.date.today()
    after = (today - dt.timedelta(days=months * 30)).isoformat()
    print("1/4 researching similar funded startups...", file=sys.stderr)
    deals = parse_deals(research(build_query(description, months, today)))
    if not deals:
        sys.exit("Research returned no parsable deals. Try a more specific description.")
    print(f"2/4 found {len(deals)} deals; filling missing sources via search...", file=sys.stderr)
    enrich_with_search(deals, after)
    if do_verify:
        print("3/4 verifying investors on live source pages...", file=sys.stderr)
        verify(deals)
    print("4/4 ranking investors...", file=sys.stderr)
    return rank(deals, today, months), deals


DEMO = [
    Deal("LedgerLeaf", "AI bookkeeping for informal retailers in Nigeria", "$2.1M", "Seed", "2026-06",
         ["Sample Fund A", "Sample Accelerator", "Sample Angels"], ["Sample Fund A"],
         ["https://example.com/ledgerleaf-seed"], 0.95, ["Sample Fund A", "Sample Accelerator"]),
    Deal("Kasha Books", "Mobile accounting for SMEs in Kenya", "$1.4M", "Pre-seed", "2025-11",
         ["Sample Angels", "Sample Fund B"], ["Sample Angels"],
         ["https://example.com/kasha-books"], 0.8, ["Sample Angels"]),
    Deal("Tally AI", "Inventory and cash-flow copilot for shops in India", "$6M", "Series A", "2026-02",
         ["Sample Accelerator", "Sample Growth"], ["Sample Growth"], ["https://example.com/tally-ai"], 0.7, ["Sample Growth"]),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("description", nargs="?", help="one-line startup description")
    ap.add_argument("--months", type=int, default=18)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default="funding_radar_report", help="output file prefix")
    ap.add_argument("--no-verify", action="store_true", help="skip live Fetch verification")
    ap.add_argument("--demo", action="store_true", help="offline demo with sample data")
    a = ap.parse_args()

    if a.demo:
        desc, deals = "AI bookkeeping assistant for small shops in Africa (demo data)", DEMO
        today = dt.date(2026, 11, 20)
        matches = rank(deals, today, a.months)
    else:
        if not a.description:
            ap.error("give a one-line description, or use --demo")
        desc = a.description
        matches, deals = run(desc, a.months, a.top, not a.no_verify)

    md, page = to_markdown(desc, matches, deals, a.top), to_html(desc, matches, deals, a.top)
    with open(a.out + ".md", "w") as f:
        f.write(md)
    with open(a.out + ".html", "w") as f:
        f.write(page)
    with open(a.out + ".json", "w") as f:
        json.dump({"description": desc, "investors": [asdict(m) for m in matches],
                   "deals": [asdict(d) for d in deals]}, f, indent=2)
    print(md)
    print(f"\nSaved {a.out}.md, {a.out}.html and {a.out}.json", file=sys.stderr)


if __name__ == "__main__":
    main()
