"""매수 후보 재무 분석 (stockanalysis.com statistics + 업종).

평가 4축 (통과 수 / 평가 가능 축 수 -> 등급)
  일반 업종
    가치  : 선행 PER(없으면 PER) 0 < x <= 25, 또는 PEG <= 1.5
    수익성: ROE >= 12%, 영업이익률 >= 10%, FCF 마진 >= 5% 중 2개 이상
    안정성: 부채비율(D/E) <= 1.0
    성장  : 3년 매출성장 전망 >= 5%
  금융업 (Sector == Financials; 부채비율·FCF 는 의미 없음)
    가치  : PBR <= 1.5 또는 PER <= 15
    수익성: ROE >= 10%
    안정성: 평가 제외
    성장  : 3년 매출성장 전망 >= 3%
  등급: 통과 비율 >= 75% A, >= 50% B, >= 25% C, 그 외 D. 평가 가능 축 < 2 이면 N/A
캐시: fund_cache.json, 종목별 7일
"""
import datetime as dt
import html
import json
import os
import re
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "fund_cache.json")
TTL_DAYS = 7
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
FIELDS = {
    "pe": "PE Ratio", "fpe": "Forward PE", "pb": "PB Ratio", "peg": "PEG Ratio",
    "roe": "Return on Equity (ROE)", "opm": "Operating Margin", "fcfm": "FCF Margin",
    "de": "Debt / Equity", "growth": "Revenue Growth Forecast (3Y)", "dy": "Dividend Yield",
}


def _url(sym):
    if sym.endswith(".KS") or sym.endswith(".KQ"):
        return f"https://stockanalysis.com/quote/krx/{sym[:6]}/"
    return f"https://stockanalysis.com/stocks/{sym.lower().replace('-', '.')}/"


def _text(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        s = r.read().decode("utf-8", "ignore")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "|", s))
    return re.sub(r"\|[\s|]*\|", "|", t)


def _num(v):
    if v is None:
        return None
    v = v.strip().replace(",", "").rstrip("%")
    try:
        return float(v)
    except ValueError:
        return None


def fetch(sym):
    t = _text(_url(sym) + "statistics/")
    d = {}
    for k, label in FIELDS.items():
        m = re.search(re.escape(label) + r"\|([^|]{1,20})\|", t)
        d[k] = _num(m.group(1)) if m else None
    if all(v is None for v in d.values()):
        raise ValueError("no statistics")
    m = re.search(r"\|Sector\|([^|]{1,40})\|", _text(_url(sym)))
    d["sector"] = m.group(1).strip() if m else None
    return d


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_many(syms, log=None):
    """{sym: data | None}. 캐시 7일, 실패 시 오래된 캐시라도 사용."""
    cache = _load_cache()
    now = dt.datetime.now()
    out, changed, fails = {}, False, []
    for s in syms:
        c = cache.get(s)
        fresh = c and (now - dt.datetime.fromisoformat(c["ts"])).days < TTL_DAYS
        if not fresh:
            try:
                cache[s] = {"ts": now.isoformat(timespec="seconds"), "data": fetch(s)}
                changed = True
            except Exception as e:
                fails.append(f"{s}({e})")
        out[s] = cache[s]["data"] if s in cache else None
    if changed:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    if log is not None and fails:
        log.append("재무 조회 실패: " + ", ".join(fails))
    return out


def grade(d):
    """-> (등급, {축: True/False/None}, 금융업 여부)"""
    if not d:
        return "N/A", {}, False
    fin = (d.get("sector") or "").lower() == "financials"
    pe = d["fpe"] if d.get("fpe") and d["fpe"] > 0 else d.get("pe")
    ax = {}
    if fin:
        ax["가치"] = None if d.get("pb") is None and pe is None else (
            (d.get("pb") is not None and d["pb"] <= 1.5) or (pe is not None and 0 < pe <= 15))
        ax["수익성"] = None if d.get("roe") is None else d["roe"] >= 10
        ax["안정성"] = None
        ax["성장"] = None if d.get("growth") is None else d["growth"] >= 3
    else:
        ax["가치"] = None if pe is None and d.get("peg") is None else (
            (pe is not None and 0 < pe <= 25) or (d.get("peg") is not None and 0 < d["peg"] <= 1.5))
        prof = [x for x in ((d.get("roe"), 12), (d.get("opm"), 10), (d.get("fcfm"), 5)) if x[0] is not None]
        ax["수익성"] = None if len(prof) < 2 else sum(v >= th for v, th in prof) >= 2
        ax["안정성"] = None if d.get("de") is None else d["de"] <= 1.0
        ax["성장"] = None if d.get("growth") is None else d["growth"] >= 5
    ev = [v for v in ax.values() if v is not None]
    if len(ev) < 2:
        return "N/A", ax, fin
    r = sum(ev) / len(ev)
    return ("A" if r >= 0.75 else "B" if r >= 0.5 else "C" if r >= 0.25 else "D"), ax, fin
