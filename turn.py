"""상위 20개 종목 추세 전환 판정.

기준 (일봉, 종가):
  - 이동평균: MA20/50/200, MA200 기울기(20거래일 전 대비)
  - 스윙 구조: 좌우 PIV일 기준 피벗 고점/저점. 마지막 두 저점 비교(높은 저점),
    마지막 저점 이전의 피벗 고점(=직전 하락 고점)을 종가가 돌파했는지
  - 모멘텀: RSI14, MACD(12,26,9)
  - 거래량: 돌파일 거래량 / 직전 50일 평균
판정:
  상승추세 유지 : 종가>MA50>MA200, MA200 상승, 52주 고점 대비 -10% 이내
  상승전환 확인 : 최근 하락 이력(최근 6개월 내 고점 대비 -15% 이상 or MA200 하회) +
                  높은 저점 + 직전 고점 돌파 + 종가>MA50
  전환 시도     : 높은 저점 또는 (MACD 골든크로스 & RSI>50) 이지만 고점 돌파 전
  조정 중       : 종가>MA200 이지만 종가<MA50
  하락추세      : 종가<MA200 & MA50<MA200 (또는 낮은 고점·낮은 저점 지속)
"""
import json, re, html, sys, urllib.request, datetime as dt
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0"}
PIV = 5
import os
HOLD = sys.argv[1] if len(sys.argv) > 1 and __name__ == "__main__" else os.path.join(os.path.dirname(os.path.abspath(__file__)), "holdings")


# ETF별 분석 대상 상위 종목 수
LIMITS = {"SPY": 30, "QQQ": 30, "KODEX200": 50}


def _merge_add(res, seen, key, name, w, limit):
    """중복 티커(알파벳 A/C 등)는 비중 합산. limit 도달 시 True."""
    if key in seen:
        for x in res:
            if x[0] == key:
                x[2] += w
        return False
    seen.add(key)
    res.append([key, name, w])
    return len(res) >= limit


def _load_csv(path, limit, krx=False):
    """사용자 제공 전체 목록: ticker,name,weight(%) — 헤더 1줄, 한국 종목은 6자리 코드."""
    import csv
    res, seen = [], set()
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))[1:]
    rows = [r for r in rows if len(r) >= 3 and r[2].strip()]
    rows.sort(key=lambda r: -float(r[2].strip().rstrip("%")))
    for r in rows:
        t = r[0].strip()
        if krx:
            t = t.replace("KRX:", "").replace(".KS", "").zfill(6) + ".KS"
        else:
            t = t.replace(".", "-")
            t = "GOOGL" if t in ("GOOG", "GOOGL") else t
        if _merge_add(res, seen, t, r[1].strip(), float(r[2].strip().rstrip("%")), limit):
            break
    return res


def parse_sa(path, limit, krx=False):
    """stockanalysis.com holdings 페이지 파싱 (무료 페이지는 상위 25개까지)."""
    s = open(path, encoding="utf-8", errors="ignore").read()
    res, seen = [], set()
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
        c = [html.unescape(re.sub("<.*?>", "", x)).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        if len(c) < 4 or not c[0].isdigit():
            continue
        t = c[1].replace("KRX:", "") + (".KS" if krx else "")
        key = "GOOGL" if t in ("GOOG", "GOOGL") else t.replace(".", "-") if not krx else t
        if _merge_add(res, seen, key, c[2], float(c[3].rstrip("%")), limit):
            break
    return res


def load_lists():
    import openpyxl  # noqa
    out = {}
    # SPY
    ws = openpyxl.load_workbook(f"{HOLD}/spy.xlsx", read_only=True).active
    spy, seen = [], set()
    for row in list(ws.iter_rows(values_only=True))[5:]:
        if not row[1] or not isinstance(row[4], (int, float)):
            continue
        t = row[1].replace(".", "-")
        key = "GOOGL" if t in ("GOOG", "GOOGL") else t
        if _merge_add(spy, seen, key, row[0].title(), row[4], LIMITS["SPY"]):
            break
    out["SPY"] = spy

    sa = parse_sa
    # 전체 목록 csv(daily_report.py 가 자동 생성, 수동 제공도 가능)가 있으면 우선 사용, 없으면 stockanalysis(상위 25개까지)
    for etf, csvname, htmlname, krx in (("QQQ", "qqq_full.csv", "qqq.html", False),
                                        ("KODEX200", "k200_full.csv", "k200_sa.html", True)):
        p = os.path.join(HOLD, csvname)
        out[etf] = _load_csv(p, LIMITS[etf], krx) if os.path.exists(p) else sa(os.path.join(HOLD, htmlname), LIMITS[etf], krx)
    return out


def fetch(sym):
    p1 = int((dt.datetime.now() - dt.timedelta(days=800)).timestamp())
    p2 = int((dt.datetime.now() + dt.timedelta(days=1)).timestamp())
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d"
    r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    rows = [(dt.date.fromtimestamp(t), c, v or 0) for t, c, v in zip(r["timestamp"], q["close"], q["volume"]) if c]
    EXCH[sym] = r["meta"].get("exchangeName")  # NMS(나스닥), NYQ(NYSE), KSC(KRX) 등
    return rows


EXCH = {}


def ema(x, n):
    k = 2 / (n + 1)
    out = [x[0]]
    for v in x[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma(x, n, i):
    return sum(x[i - n + 1:i + 1]) / n if i >= n - 1 else None


def rsi(c, n=14):
    g = l = 0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
    return 100 - 100 / (1 + g / l) if l else 100


def analyze(sym):
    rows = fetch(sym)
    d = [r[0] for r in rows]; c = [r[1] for r in rows]; v = [r[2] for r in rows]
    i = len(c) - 1
    ma20, ma50, ma200 = sma(c, 20, i), sma(c, 50, i), sma(c, 200, i)
    ma200_prev = sma(c, 200, i - 20)
    if ma200 is None: ma200 = ma200_prev = sma(c, len(c)-20, i)
    ma50_prev = sma(c, 50, i - 10); ma200_prev10 = sma(c, 200, i - 10)
    golden_recent = None not in (ma50_prev, ma200_prev10) and ma50_prev <= ma200_prev10 and ma50 > ma200
    hi52 = max(c[-252:])
    dd = c[i] / hi52 - 1
    m12, m26 = ema(c, 12), ema(c, 26)
    macd = [a - b for a, b in zip(m12, m26)]
    sig = ema(macd, 9)
    macd_up = macd[-1] > sig[-1]
    r = rsi(c)
    # 피벗 (최근 9개월)
    start = max(PIV, len(c) - 190)
    piv_hi, piv_lo = [], []
    for k in range(start, len(c) - PIV):
        w = c[k - PIV:k + PIV + 1]
        if c[k] == max(w): piv_hi.append(k)
        if c[k] == min(w): piv_lo.append(k)
    # 아직 확정 전인 최근 저점(최근 PIV일 내 최저)도 후보로
    recent_low_k = min(range(len(c) - PIV, len(c)), key=lambda k: c[k])
    higher_low = len(piv_lo) >= 2 and c[piv_lo[-1]] > c[piv_lo[-2]]
    # 마지막 확정 저점 이후 아직 그 저점을 깨지 않았는지
    if piv_lo and min(c[piv_lo[-1]:]) < c[piv_lo[-1]]:
        higher_low = False
    lower_highs = len(piv_hi) >= 2 and c[piv_hi[-1]] < c[piv_hi[-2]]
    # 직전 하락 고점: 마지막 저점 이전의 마지막 피벗 고점
    brk = False; brk_vol = None; ref_hi = None
    if piv_lo:
        prev_his = [k for k in piv_hi if k < piv_lo[-1]]
        if prev_his:
            kh = prev_his[-1]; ref_hi = c[kh]
            for k in range(piv_lo[-1], len(c)):
                if c[k] > ref_hi:
                    brk = True
                    avg = sum(v[k - 50:k]) / 50 if k >= 50 else None
                    brk_vol = v[k] / avg if avg else None
                    break
            if brk and c[-1] < ref_hi * 0.97:
                brk = False  # 돌파 후 되밀림
    six = c[-126:]
    pk6 = max(six); lo_after = min(six[six.index(pk6):])
    was_down = (lo_after / pk6 - 1) <= -0.15 or min(c[-63:]) < ma200
    # 판정
    above50, above200 = c[i] > ma50, (ma200 is not None and c[i] > ma200)
    gc = ma200 is not None and ma50 > ma200
    up200 = ma200 is not None and ma200_prev is not None and ma200 > ma200_prev
    if above50 and above200 and gc and up200 and dd > -0.10:
        state = "상승추세 유지"
    elif was_down and brk and higher_low and above50:
        state = "상승전환 확인"
    elif above50 and above200 and not gc:
        state = "전환 진행(골든크로스 대기)"
    elif above50 and above200:
        state = "반등 중(고점 미돌파)"
    elif above200 and not above50:
        state = "조정 중(장기추세 유지)"
    elif above50 and not above200:
        state = "바닥 반등 시도"
    else:
        state = "하락추세" + ("(초기)" if gc else "") + (" + 반등 신호" if (macd_up and r > 45) or higher_low else "")
    conf = ref_hi if brk else (c[piv_hi[-1]] if piv_hi else None)
    score = sum([c[i] > ma50, c[i] > ma200, ma50 > ma200, ma200 > ma200_prev,
                 higher_low, brk, macd_up, r > 50])
    return dict(sym=sym, exch=EXCH.get(sym), last_low=c[piv_lo[-1]] if piv_lo else None, last=c[i], date=str(d[i]), dd=dd * 100, a50=c[i] > ma50, a200=c[i] > ma200,
                gc=ma50 > ma200, golden_recent=golden_recent, ma200_up=ma200 > ma200_prev,
                hl=higher_low, lh=lower_highs, brk=brk, brk_vol=brk_vol, macd=macd_up, rsi=r,
                score=score, state=state, conf=conf, conf_gap=(conf/c[i]-1)*100 if conf else None, short=len(c)<220, ref_hi=ref_hi, ma50=ma50, ma200=ma200)


def _run(s):
    try:
        return s, analyze(s)
    except Exception as e:
        return s, {"error": str(e)}


def run_all():
    lists = load_lists()
    syms = sorted({x[0] for L in lists.values() for x in L})
    out = {}
    with ThreadPoolExecutor(8) as ex:
        for s, a in ex.map(_run, syms):
            out[s] = a
    return lists, out


if __name__ == "__main__":
    lists, out = run_all()
    json.dump({"lists": lists, "res": out}, open(f"{HOLD}/../turn.json", "w"), ensure_ascii=False, indent=1, default=str)
    for etf, L in lists.items():
        print(f"\n=== {etf} ===")
        for t, name, w in L:
            a = out[t]
            if "error" in a: print(t, "ERR", a["error"]); continue
            f = lambda b: "O" if b else "-"
            print(f"{t:10s} {w:5.2f}% | {a['state']:22s} | dd{a['dd']:6.1f}% >50{f(a['a50'])} >200{f(a['a200'])} GC{f(a['gc'])} HL{f(a['hl'])} BRK{f(a['brk'])} MACD{f(a['macd'])} RSI{a['rsi']:.0f} | conf {a['conf'] and round(a['conf'],2)} ({a['conf_gap'] and round(a['conf_gap'],1)}%) last {a['last']:.2f}{' SHORT' if a['short'] else ''}")
