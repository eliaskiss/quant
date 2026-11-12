"""매일 아침 상승전환 종목 리포트(HTML) 생성.

1. holdings/ 의 보유종목 파일 갱신 (실패 시 기존 파일 유지)
2. turn.run_all() 로 SPY / QQQ / KODEX200 상위 20개 추세 판정
3. daily_report/YYYY-MM-DD.html 저장 (+ latest.html), 판정 결과는 daily_report/data/YYYY-MM-DD.json
4. 직전 리포트 JSON 과 비교해 신규 상승전환 / 이탈 종목 표시
"""
import datetime as dt
import glob
import html
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
HOLD = os.path.join(BASE, "holdings")
OUT = os.path.join(BASE, "daily_report")
DATA = os.path.join(OUT, "data")
sys.path.insert(0, BASE)
import turn  # noqa: E402
import fundamentals  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
SOURCES = {
    "spy.xlsx": "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx",
    "qqq.html": "https://stockanalysis.com/etf/qqq/holdings/",
    "k200_sa.html": "https://stockanalysis.com/quote/krx/069500/holdings/",
}
KR_NAMES = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "402340.KS": "SK스퀘어", "009150.KS": "삼성전기",
    "105560.KS": "KB금융", "005380.KS": "현대차", "055550.KS": "신한지주", "068270.KS": "셀트리온",
    "086790.KS": "하나금융지주", "006400.KS": "삼성SDI", "028260.KS": "삼성물산", "035420.KS": "NAVER",
    "000270.KS": "기아", "012330.KS": "현대모비스", "012450.KS": "한화에어로스페이스", "316140.KS": "우리금융지주",
    "032830.KS": "삼성생명", "034020.KS": "두산에너빌리티", "005490.KS": "POSCO홀딩스", "000810.KS": "삼성화재",
    "034730.KS": "SK", "373220.KS": "LG에너지솔루션", "207940.KS": "삼성바이오로직스", "051910.KS": "LG화학",
    "035720.KS": "카카오", "015760.KS": "한국전력", "329180.KS": "HD현대중공업", "042660.KS": "한화오션",
    "267260.KS": "HD현대일렉트릭", "010130.KS": "고려아연", "096770.KS": "SK이노베이션", "066570.KS": "LG전자",
}
STATE_ORDER = ["상승전환 확인", "전환 진행", "바닥 반등 시도", "반등 중", "상승추세 유지", "조정 중", "하락추세", "판정 불가"]
STATE_COLOR = {"상승전환 확인": "--up", "전환 진행": "--turn", "바닥 반등 시도": "--turn", "반등 중": "--turn2",
               "상승추세 유지": "--trend", "조정 중": "--warn", "하락추세": "--down", "판정 불가": "--muted"}


def refresh_holdings(log):
    for fn, url in SOURCES.items():
        dst = os.path.join(HOLD, fn)
        tmp = dst + ".tmp"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(tmp, "wb") as f:
                f.write(r.read())
            if os.path.getsize(tmp) < 20_000:
                raise ValueError(f"too small ({os.path.getsize(tmp)} bytes)")
            os.replace(tmp, dst)
            log.append(f"{fn}: 갱신")
        except Exception as e:  # 기존 파일 유지
            if os.path.exists(tmp):
                os.remove(tmp)
            log.append(f"{fn}: 갱신 실패, 기존 파일 사용 ({e})")


def _get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, "Accept": "*/*"}), timeout=60) as r:
        return json.load(r)


def _write_csv(path, rows):
    import csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "weight"])
        w.writerows(rows)


def build_k200_csv(log):
    """삼성자산운용 KODEX 200(2ETF01) PDF API -> holdings/k200_full.csv (전 종목, 한글명, 실제 비중)."""
    for back in range(0, 7):
        day = (dt.date.today() - dt.timedelta(days=back)).strftime("%Y.%m.%d")
        try:
            pdf = _get_json(f"https://www.samsungfund.com/api/v1/kodex/product-pdf/2ETF01.do?gijunYMD={day}")["pdf"]
            rows = [(x["itmNo"], x["secNm"], x["ratio"]) for x in pdf["list"]
                    if x.get("ratio") and len(x.get("itmNo", "")) == 6 and x["itmNo"].isdigit()]
            if len(rows) < 100:
                raise ValueError(f"{len(rows)} rows")
            _write_csv(os.path.join(HOLD, "k200_full.csv"), rows)
            for code, name, _ in rows:
                KR_NAMES.setdefault(code + ".KS", name)
            log.append(f"k200_full.csv: 삼성자산운용 PDF {pdf['gijunYMD']} 기준 {len(rows)}종목")
            return
        except Exception as e:
            err = e
    log.append(f"k200_full.csv: 갱신 실패, 기존 파일 사용 ({err})")


def build_qqq_csv(log):
    """Nasdaq-100 구성종목(시가총액) -> holdings/qqq_full.csv.
    stockanalysis 상위 25개는 실제 비중을 쓰고, 나머지는 시총 비중을 실제 비중 스케일로 보정한 추정치."""
    try:
        n = _get_json("https://api.nasdaq.com/api/quote/list-type/nasdaq100")["data"]
        caps = {}
        names = {}
        for r in n["data"]["rows"]:
            t = r["symbol"].replace(".", "-")
            t = "GOOGL" if t in ("GOOG", "GOOGL") else t
            caps[t] = caps.get(t, 0) + float(r["marketCap"].replace(",", "") or 0)
            names[t] = r["companyName"].replace(" Common Stock", "").replace(" Class A", "").strip()
        tot = sum(caps.values())
        share = {t: c / tot * 100 for t, c in caps.items()}
        actual = {}
        qh = os.path.join(HOLD, "qqq.html")
        if os.path.exists(qh):
            for t, _, w in turn.parse_sa(qh, 100):
                actual[t] = w
        overlap = [t for t in actual if t in share]
        # 실제 비중이 있는 종목 중 하위 절반으로 스케일(상위 대형주는 캡 규칙 영향이 커서 제외)
        tail = sorted(overlap, key=lambda t: actual[t])[: max(1, len(overlap) // 2)]
        scale = sum(actual[t] for t in tail) / sum(share[t] for t in tail) if tail else 1.0
        rows = [(t, names[t], f"{actual.get(t, share[t] * scale):.4f}") for t in caps]
        _write_csv(os.path.join(HOLD, "qqq_full.csv"), rows)
        log.append(f"qqq_full.csv: Nasdaq-100 {n['date']} 기준 {len(rows)}종목 (실제 비중 {len(overlap)}개, 나머지 시총 추정)")
    except Exception as e:
        log.append(f"qqq_full.csv: 갱신 실패, 기존 파일 사용 ({e})")


def group(state):
    for g in STATE_ORDER:
        if state.startswith(g):
            return g
    return "판정 불가"


NAME_CACHE = os.path.join(HOLD, "kr_names.json")


def load_kr_names(tickers, log):
    """KR_NAMES 에 없는 한국 종목은 네이버 종목 기본정보에서 한글명을 받아 캐시."""
    cache = {}
    if os.path.exists(NAME_CACHE):
        with open(NAME_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    missing = [t for t in tickers if t.endswith(".KS") and t not in KR_NAMES and t not in cache]
    for t in missing:
        try:
            u = f"https://m.stock.naver.com/api/stock/{t[:6]}/basic"
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
                cache[t] = json.load(r)["stockName"]
        except Exception as e:
            log.append(f"{t} 한글명 조회 실패 ({e})")
    if missing:
        with open(NAME_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    KR_NAMES.update(cache)


def name_of(t, name):
    return KR_NAMES.get(t, name)


def fmt_px(t, x):
    if x is None:
        return "-"
    return f"{x:,.0f}원" if t.endswith(".KS") else f"${x:,.2f}"


def prev_snapshot(today):
    files = sorted(f for f in glob.glob(os.path.join(DATA, "*.json")) if os.path.basename(f) < f"{today}.json")
    if not files:
        return None, None
    with open(files[-1], encoding="utf-8") as f:
        return os.path.basename(files[-1])[:-5], json.load(f)


def badge(state):
    g = group(state)
    return f'<span class="badge" style="--c:var({STATE_COLOR[g]})">{html.escape(state)}</span>'


def yn(b):
    return '<span class="y">●</span>' if b else '<span class="n">○</span>'


TV_CHART = "https://kr.tradingview.com/chart/dt1Wuwdx/?symbol="
# Yahoo exchangeName -> TradingView 거래소 접두어
TV_EXCH = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ", "NasdaqGS": "NASDAQ",
           "NYQ": "NYSE", "NYS": "NYSE", "NYSE": "NYSE", "PCX": "AMEX", "ASE": "AMEX", "BTS": "CBOE"}


def tv_url(t, exch=None):
    if t.endswith(".KS") or t.endswith(".KQ"):
        sym = f"KRX:{t[:6]}"
    else:
        sym = f"{TV_EXCH.get(exch or '', 'NASDAQ')}:{t.replace('-', '.')}"  # BRK-B -> NYSE:BRK.B
    return TV_CHART + urllib.parse.quote(sym, safe="")


def name_link(t, name, exch=None):
    return (f'<a href="{html.escape(tv_url(t, exch))}" target="_blank" rel="noopener">'
            f"<b>{html.escape(name_of(t, name))}</b></a>")


def row(t, name, etfs, a, new=False):
    gap = a.get("conf_gap")
    gap_s = "-" if gap is None else (f"돌파" if gap <= 0 else f"+{gap:.1f}%")
    vol = a.get("brk_vol")
    return (f"<tr><td>{name_link(t, name, a.get('exch'))}{' <span class=new>NEW</span>' if new else ''}"
            f"<div class=sub>{html.escape(t)} · {html.escape(etfs)}</div></td>"
            f"<td>{badge(a['state'])}</td><td class=num>{fmt_px(t, a['last'])}</td>"
            f"<td class=num>{a['dd']:.1f}%</td><td class=num>{a['rsi']:.0f}</td>"
            f"<td class=c>{yn(a['a50'])}</td><td class=c>{yn(a['a200'])}</td><td class=c>{yn(a['gc'])}</td>"
            f"<td class=c>{yn(a['hl'])}</td><td class=c>{yn(a['brk'])}</td><td class=c>{yn(a['macd'])}</td>"
            f"<td class=num>{fmt_px(t, a.get('conf'))}<div class=sub>{gap_s}</div></td>"
            f"<td class=num>{'-' if vol is None else f'{vol:.1f}x'}</td></tr>")


THEAD = ("<thead><tr><th>종목</th><th>판정</th><th>현재가</th><th>52주 고점 대비</th><th>RSI</th>"
         "<th>&gt;50일선</th><th>&gt;200일선</th><th>골든크로스</th><th>높은 저점</th><th>고점 돌파</th><th>MACD↑</th>"
         "<th>전환 기준가</th><th>돌파 거래량</th></tr></thead>")


def table(items, empty="해당 종목 없음"):
    if not items:
        return f'<p class="empty">{empty}</p>'
    return f'<div class="tw"><table>{THEAD}<tbody>{"".join(items)}</tbody></table></div>'


ETF_SYMBOL = {"SPY": "SPY", "QQQ": "QQQ", "KODEX200": "069500.KS"}


def _etf_agg(lists, res):
    """ETF별 {상태그룹: 비중합}, 상위N 비중합."""
    out = {}
    for etf, L in lists.items():
        agg = {}
        for t, _, w in L:
            a = res.get(t)
            k = group(a["state"]) if a and "error" not in a else "판정 불가"
            agg[k] = agg.get(k, 0) + w
        out[etf] = (agg, sum(agg.values()))
    return out


GRADE_RANK = {"A": 4, "B": 3, "C": 2, "N/A": 1, "D": 0}
GRADE_COLOR = {"A": "--up", "B": "--trend", "C": "--warn", "D": "--down", "N/A": "--muted"}


def closing_section(lists, ok, meta, etf_self, prev, log):
    """리포트 마지막: ETF별 추세 요약 + 규칙 기반 매수 후보."""
    cur = _etf_agg(lists, ok)
    old = _etf_agg(prev["lists"], prev["res"]) if prev else {}

    def pct_up(agg, tot):
        return (agg.get("상승전환 확인", 0) + agg.get("상승추세 유지", 0)) / tot * 100 if tot else 0

    def pct_dn(agg, tot):
        return agg.get("하락추세", 0) / tot * 100 if tot else 0

    # ── ETF별 추세 요약
    rows = []
    for etf, L in lists.items():
        agg, tot = cur[etf]
        up, dn = pct_up(agg, tot), pct_dn(agg, tot)
        delta = ""
        if etf in old:
            d = up - pct_up(*old[etf])
            delta = f'<div class=sub>전일 대비 {"+" if d >= 0 else ""}{d:.1f}%p</div>'
        if up >= 60:
            verdict, color, act = "강세", "--trend", "적립식 유지, 눌림목 추가매수 유효"
        elif dn >= 60:
            verdict, color, act = "약세", "--down", "신규 목돈 보류, 소액 분할만. 대형 비중 종목의 전환 기준가 돌파 확인 후 확대"
        else:
            verdict, color, act = "혼조", "--warn", "지수 전체보다 상승추세 종목 선별 접근"
        s = etf_self.get(etf)
        self_s = (f"{badge(s['state'])}<div class=sub>고점 대비 {s['dd']:.1f}% · RSI {s['rsi']:.0f}"
                  f"{' · 50일선 위' if s['a50'] else ' · 50일선 아래'}{' · 200일선 위' if s['a200'] else ' · 200일선 아래'}</div>"
                  if s and "error" not in s else "-")
        top3 = " · ".join(f"{html.escape(name_of(t, n))} <span style='color:var({STATE_COLOR[group(ok[t]['state'])]})'>"
                          f"{html.escape(group(ok[t]['state']))}</span>"
                          for t, n, w in L[:3] if ok.get(t))
        rows.append(f"<tr><td><b>{etf}</b></td><td>{self_s}</td>"
                    f"<td class=num><b style='color:var(--trend)'>{up:.1f}%</b>{delta}</td>"
                    f"<td class=num><b style='color:var(--down)'>{dn:.1f}%</b></td>"
                    f"<td style='white-space:normal'>{top3}</td>"
                    f"<td style='white-space:normal'><span class=badge style='--c:var({color})'>{verdict}</span> {act}</td></tr>")
    etf_tbl = ("<div class=tw><table><thead><tr><th>ETF</th><th>ETF 자체 추세</th><th>상승 비중<br>(전환+유지)</th>"
               "<th>하락추세 비중</th><th>상위 3종목 상태</th><th>판단</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")

    # ── 매수 후보 (규칙 기반)
    def wmax(t):
        return max(float(e.split()[-1][:-1]) for e in meta[t]["etfs"])

    fund = {}

    def fund_cell(t):
        d = fund.get(t)
        gr, ax, fin = fundamentals.grade(d)
        if not d:
            return f"<span class=badge style='--c:var(--muted)'>N/A</span><div class=sub>재무 데이터 없음</div>"
        pe = d["fpe"] if d.get("fpe") and d["fpe"] > 0 else d.get("pe")
        f = lambda v, suf="": "-" if v is None else f"{v:.1f}{suf}"
        if fin:
            nums = f"PER {f(pe)} · PBR {f(d.get('pb'))} · ROE {f(d.get('roe'), '%')} · 성장 {f(d.get('growth'), '%')}"
        else:
            nums = (f"PER {f(pe)} · PEG {f(d.get('peg'))} · ROE {f(d.get('roe'), '%')} · 영업이익률 {f(d.get('opm'), '%')}"
                    f" · 부채비율 {f(d.get('de'))} · 성장 {f(d.get('growth'), '%')}")
        axes = " ".join(f"{k}{'✓' if v else '✗' if v is False else '–'}" for k, v in ax.items())
        return (f"<span class=badge style='--c:var({GRADE_COLOR[gr]})'>{gr}</span> "
                f"<span class=sub>{axes}{' · 금융업 기준' if fin else ''}</span><div class=sub>{nums}</div>")

    def rec_row(t, entry, stop, why):
        a = ok[t]
        risk = (entry - stop) / entry * 100 if entry and stop and stop < entry else None
        return (f"<tr><td>{name_link(t, meta[t]['name'], a.get('exch'))}<div class=sub>{html.escape(', '.join(meta[t]['etfs']))}</div></td>"
                f"<td class=num>{fmt_px(t, a['last'])}</td><td class=num>{entry_label(t, entry, a)}</td>"
                f"<td class=num>{fmt_px(t, stop)}</td><td class=num>{'-' if risk is None else f'{risk:.1f}%'}</td>"
                f"<td style='white-space:normal;min-width:260px'>{fund_cell(t)}</td>"
                f"<td style='white-space:normal'>{why}</td></tr>")

    def entry_label(t, entry, a):
        return "현재가 부근" if abs(entry / a["last"] - 1) < 0.005 else f"{fmt_px(t, entry)} 돌파 시"

    A, B, C, hot = [], [], [], []
    for t, a in ok.items():
        if not a:
            continue
        g = group(a["state"])
        ext = (a["last"] / a["conf"] - 1) * 100 if a.get("conf") else None
        if g in ("상승전환 확인", "상승추세 유지", "전환 진행") and a["rsi"] > 75:
            hot.append(t)
            continue
        if g == "상승전환 확인" and a["rsi"] <= 72 and ext is not None and ext <= 8:
            stop = a["last_low"] if a.get("last_low") and a["last_low"] < a["last"] else a["conf"] * 0.95
            vol = a.get("brk_vol")
            why = (f"높은 저점 + 직전 고점 돌파 (기준가 대비 +{ext:.1f}%)"
                   + (f", 돌파 거래량 {vol:.1f}배" if vol else "") + f", RSI {a['rsi']:.0f}")
            A.append(((vol or 0) + wmax(t) / 10, t, a["last"], stop, why))
        elif g == "상승추세 유지" and 40 <= a["rsi"] <= 62 and a.get("ma50") and 0 <= a["last"] / a["ma50"] - 1 <= 0.04:
            gap50 = (a["last"] / a["ma50"] - 1) * 100
            why = f"상승추세 중 50일선 부근 눌림 (50일선 +{gap50:.1f}%), RSI {a['rsi']:.0f}"
            B.append((wmax(t), t, a["last"], a["ma50"] * 0.97, why))
        elif (g in ("전환 진행", "바닥 반등 시도", "반등 중") or (g in ("하락추세", "조정 중") and a["a50"])) \
                and a.get("conf_gap") is not None and 0 < a["conf_gap"] <= 3 and a.get("last_low"):
            why = f"{html.escape(a['state'])}, 전환 기준가까지 +{a['conf_gap']:.1f}% — 거래량 동반 돌파 확인 후 진입"
            C.append((-a["conf_gap"], t, a["conf"], a["last_low"], why))

    # 재무 분석: 후보 종목만 조회(7일 캐시). D 등급은 추천에서 제외해 '재무 취약'으로 분리
    fund.update(fundamentals.get_many([x[1] for x in A + B + C], log))
    weak = []

    def rank(items, label):
        keep = []
        for key, t, entry, stop, why in items:
            gr = fundamentals.grade(fund.get(t))[0]
            if gr == "D":
                weak.append((t, label))
                continue
            keep.append((GRADE_RANK[gr], key, t, entry, stop, why))
        keep.sort(key=lambda x: (-x[0], -x[1]))  # 재무 등급 우선, 같은 등급은 기술 점수 순
        return [rec_row(t, entry, stop, why) for _, _, t, entry, stop, why in keep[:6]]

    A, B, C = rank(A, "추세전환"), rank(B, "눌림목"), rank(C, "돌파 대기")

    def rec_tbl(items, empty):
        if not items:
            return f"<p class=empty>{empty}</p>"
        return ("<div class=tw><table><thead><tr><th>종목</th><th>현재가</th><th>매수 기준</th><th>손절 기준</th>"
                "<th>위험폭</th><th>재무</th><th>기술적 근거</th></tr></thead><tbody>" + "".join(items) + "</tbody></table></div>")

    weak_html = ""
    if weak:
        weak_html = ("<p class=note>🚫 기술적 조건은 맞지만 재무 D등급(평가 축 대부분 미달)이라 제외: " +
                     ", ".join(f"{html.escape(name_of(t, meta[t]['name']))}({lb})" for t, lb in weak) + "</p>")

    hot_html = ""
    if hot:
        hot_html = ("<p class=note>⚠️ 추세는 좋지만 단기 과열(RSI&gt;75)로 추격매수 주의: " +
                    ", ".join(f"{html.escape(name_of(t, meta[t]['name']))}(RSI {ok[t]['rsi']:.0f})" for t in hot) + "</p>")

    return f"""
<h2>📋 ETF별 추세 요약</h2>
<p class="sub">상승/하락 비중은 분석 대상 상위 종목의 ETF 내 비중 합 기준 · 강세: 상승 60% 이상 · 약세: 하락추세 60% 이상</p>
{etf_tbl}

<h2>🎯 매수 추천 종목 (기술적 + 재무 규칙 기반)</h2>
<p class="sub">구분별 최대 6개, 재무 등급 우선·같은 등급은 기술 점수 순 · 손절 기준을 이탈하면 추세 판단이 틀린 것으로 보고 정리 · 위험폭 = (매수 기준 − 손절 기준) ÷ 매수 기준</p>
<div class="tw"><table class="crit"><tbody>
<tr><td><b>재무 등급</b></td><td>4개 축 통과 비율 — A 75%↑ · B 50%↑ · C 25%↑ · D 미만(추천 제외) · N/A 데이터 부족 &nbsp; (✓ 통과 ✗ 미달 – 평가 제외)</td></tr>
<tr><td>일반 업종</td><td>가치: 선행 PER ≤ 25 또는 PEG ≤ 1.5 · 수익성: ROE ≥ 12%, 영업이익률 ≥ 10%, FCF 마진 ≥ 5% 중 2개 · 안정성: 부채비율 ≤ 1.0 · 성장: 3년 매출성장 전망 ≥ 5%</td></tr>
<tr><td>금융업</td><td>가치: PBR ≤ 1.5 또는 PER ≤ 15 · 수익성: ROE ≥ 10% · 안정성: 평가 제외 · 성장: 3년 매출성장 전망 ≥ 3%</td></tr>
</tbody></table></div>
<h3>① 추세전환 매수 — 상승전환 확인 종목</h3>
<p class="sub">조건: 상승전환 확인 + RSI 72 이하 + 돌파 기준가 대비 +8% 이내(추격 아님) · 손절: 최근 높은 저점</p>
{rec_tbl(A, "조건을 만족하는 종목 없음")}
<h3>② 눌림목 매수 — 상승추세 유지 종목</h3>
<p class="sub">조건: 상승추세 유지 + RSI 40~62 + 50일선 위 0~4% 이내 · 손절: 50일선 -3%</p>
{rec_tbl(B, "조건을 만족하는 종목 없음")}
<h3>③ 돌파 대기 — 조건부 매수</h3>
<p class="sub">조건: 전환 진행·반등 중(또는 50일선 위 회복) + 전환 기준가까지 3% 이내 · 기준가를 거래량 동반 종가 돌파 시 매수 · 손절: 최근 저점</p>
{rec_tbl(C, "조건을 만족하는 종목 없음")}
{weak_html}
{hot_html}
<p class="note">※ 기술적 지표와 재무 지표(stockanalysis.com, 분기 기준·7일 캐시)로 기계적으로 고른 후보이며 투자 자문이 아닙니다. 최근 실적 발표·뉴스·포트폴리오 비중을 함께 확인하세요.</p>
"""


def build(lists, res, log, etf_self=None):
    now = dt.datetime.now()
    today = now.strftime("%Y-%m-%d")
    prev_day, prev = prev_snapshot(today)
    prev_res = (prev or {}).get("res", {})

    # 종목 -> 소속 ETF / 이름
    meta = {}
    for etf, L in lists.items():
        for t, name, w in L:
            m = meta.setdefault(t, {"name": name, "etfs": []})
            m["etfs"].append(f"{etf} {w:.2f}%")
    ok = {t: a for t, a in res.items() if "error" not in a}
    for t, a in res.items():
        if "error" in a:
            ok[t] = None
    data_date = max((a["date"] for a in ok.values() if a), default="-")

    def g(t):
        return group(ok[t]["state"]) if ok.get(t) else "판정 불가"

    def mk(ts, new_set=frozenset()):
        ts = sorted(ts, key=lambda t: -max(float(e.split()[-1][:-1]) for e in meta[t]["etfs"]))
        return [row(t, meta[t]["name"], ", ".join(meta[t]["etfs"]), ok[t], t in new_set) for t in ts]

    up = [t for t in ok if ok[t] and g(t) == "상승전환 확인"]
    prev_up = {t for t, a in prev_res.items() if a and "error" not in a and group(a["state"]) == "상승전환 확인"}
    new_up = {t for t in up if prev and t not in prev_up}
    lost_up = [t for t in prev_up if t in ok and ok[t] and g(t) != "상승전환 확인"]
    turning = [t for t in ok if ok[t] and g(t) in ("전환 진행", "바닥 반등 시도")]
    near = [t for t in ok if ok[t] and g(t) in ("하락추세", "반등 중", "조정 중")
            and ok[t].get("conf_gap") is not None and 0 < ok[t]["conf_gap"] <= 5]
    near.sort(key=lambda t: ok[t]["conf_gap"])

    # ETF별 상태 비중
    summary = []
    for etf, L in lists.items():
        agg = {}
        for t, name, w in L:
            agg[g(t)] = agg.get(g(t), 0) + w
        tot = sum(agg.values())
        bar = "".join(f'<span style="width:{v / tot * 100:.2f}%;background:var({STATE_COLOR[k]})" title="{k} {v:.1f}%"></span>'
                      for k, v in sorted(agg.items(), key=lambda kv: STATE_ORDER.index(kv[0])))
        legend = " · ".join(f'<span class="dot" style="--c:var({STATE_COLOR[k]})"></span>{k} {v:.1f}%'
                            for k, v in sorted(agg.items(), key=lambda kv: STATE_ORDER.index(kv[0])))
        upw = agg.get("상승전환 확인", 0) + agg.get("상승추세 유지", 0)
        dnw = agg.get("하락추세", 0)
        summary.append(f'<div class="card"><div class="ct"><b>{etf}</b><span class=sub>상위 {len(L)}개 비중 {tot:.1f}%</span></div>'
                       f'<div class="kpi"><div><span class=lbl>상승 (전환+유지)</span><b style="color:var(--trend)">{upw:.1f}%</b></div>'
                       f'<div><span class=lbl>하락추세</span><b style="color:var(--down)">{dnw:.1f}%</b></div></div>'
                       f'<div class="bar">{bar}</div><div class="legend">{legend}</div></div>')

    all_rows = []
    for etf, L in lists.items():
        rows_ = [row(t, name, f"{etf} {w:.2f}%", ok[t]) if ok.get(t) else
                 f"<tr><td>{name_link(t, name)}<div class=sub>{t}</div></td><td colspan=12>"
                 f"{badge('판정 불가')} {html.escape(res[t].get('error', ''))}</td></tr>" for t, name, w in L]
        all_rows.append(f"<details><summary>{etf} 상위 {len(L)}개 전체</summary>{table(rows_)}</details>")
    scope = " · ".join(f"{etf} 상위 {len(L)}개" + (f"(요청 {turn.LIMITS[etf]}개, 소스 한계)" if len(L) < turn.LIMITS[etf] else "")
                       for etf, L in lists.items())

    final_html = closing_section(lists, ok, meta, etf_self or {}, prev, log)

    lost_html = ""
    if lost_up:
        lost_html = ("<p class=note>전일 상승전환 확인에서 이탈: " +
                     ", ".join(f"{html.escape(name_of(t, meta[t]['name']))} → {html.escape(ok[t]['state'])}" for t in lost_up) + "</p>")

    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>상승전환 리포트 {today}</title>
<style>
:root{{--bg:#f7f7f5;--fg:#1d1d1f;--sub:#6b6b70;--card:#fff;--line:#e4e4e0;
--up:#0a8f5a;--trend:#2f7d4f;--turn:#c98a00;--turn2:#d4a64a;--warn:#c4682b;--down:#c0392b;--muted:#9a9a9a}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#141416;--fg:#ececee;--sub:#9a9aa2;--card:#1e1e22;--line:#2e2e34;
--up:#3fcf8e;--trend:#5fb37f;--turn:#e5b23a;--turn2:#d9b86a;--warn:#e08a52;--down:#ef6a5a;--muted:#77777d}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:24px 16px 48px}}h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:17px;margin:32px 0 10px}}h3{{font-size:15px;margin:20px 0 4px}}
.sub{{color:var(--sub);font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-top:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}.ct{{display:flex;justify-content:space-between;align-items:baseline}}
.kpi{{display:flex;gap:24px;margin:10px 0}}.kpi b{{font-size:20px;display:block}}.lbl{{font-size:12px;color:var(--sub)}}
.bar{{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--line)}}.legend{{font-size:11.5px;color:var(--sub);margin-top:8px}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--c);margin-right:4px}}
.tw{{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}}table{{border-collapse:collapse;width:100%;min-width:980px}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}}th{{font-size:12px;color:var(--sub);font-weight:600}}
tr:last-child td{{border-bottom:0}}.num{{text-align:right;font-variant-numeric:tabular-nums}}.c{{text-align:center}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;color:var(--c);border:1px solid var(--c)}}
.y{{color:var(--up)}}.n{{color:var(--muted)}}.new{{background:var(--up);color:#fff;font-size:10px;padding:1px 5px;border-radius:4px;vertical-align:middle}}
.empty,.note{{color:var(--sub)}}details{{margin:10px 0}}summary{{cursor:pointer;font-weight:600;padding:6px 0}}
.crit td{{white-space:normal}}td a{{color:inherit;text-decoration:none;border-bottom:1px dotted var(--sub)}}td a:hover{{color:var(--up);border-bottom-color:var(--up)}}footer{{margin-top:36px;color:var(--sub);font-size:12px}}
</style></head><body><main>
<h1>상승전환 리포트 · {today}</h1>
<div class="sub">데이터 기준 {data_date} 종가 · 생성 {now.strftime('%Y-%m-%d %H:%M')} · 비교 대상 {prev_day or '없음(첫 리포트)'} · 대상: {scope}</div>
<div class="grid">{''.join(summary)}</div>

<h2>🟢 상승전환 확인 ({len(up)}종목{f', 신규 {len(new_up)}' if new_up else ''})</h2>
<p class="sub">최근 하락(고점 대비 -15% 이상 또는 200일선 이탈) 후 높은 저점 형성 + 직전 고점 돌파 + 50일선 위</p>
{table(mk(up, new_up))}{lost_html}

<h2>🔄 전환 진행 ({len(turning)}종목)</h2>
<p class="sub">주가가 이동평균선 위로 올라섰지만 골든크로스(50일선 &gt; 200일선) 전, 또는 200일선 회복 전</p>
{table(mk(turning))}

<h2>👀 전환 임박 관찰 ({len(near)}종목)</h2>
<p class="sub">아직 하락/조정 상태이지만 전환 기준가(직전 고점)까지 5% 이내 · 기준가 돌파 시 거래량 동반 여부 확인 필요</p>
{table([row(t, meta[t]['name'], ', '.join(meta[t]['etfs']), ok[t]) for t in near])}

<h2>전체 종목</h2>
{''.join(all_rows)}

<h2>판정 기준</h2>
<div class="tw"><table class="crit"><tbody>
<tr><td>{badge('상승추세 유지')}</td><td>종가 &gt; 50일선 &gt; 200일선, 200일선 상승 중, 52주 고점 대비 -10% 이내</td></tr>
<tr><td>{badge('상승전환 확인')}</td><td>최근 하락 이력 + 높은 저점 + 직전 고점(마지막 저점 이전 피벗 고점) 종가 돌파 + 50일선 위 (돌파 후 -3% 이상 되밀리면 무효)</td></tr>
<tr><td>{badge('전환 진행(골든크로스 대기)')}</td><td>종가가 50일·200일선 위, 50일선은 아직 200일선 아래</td></tr>
<tr><td>{badge('반등 중(고점 미돌파)')} {badge('조정 중(장기추세 유지)')}</td><td>200일선 위에서 단기 고점 미돌파 / 50일선 하회</td></tr>
<tr><td>{badge('하락추세')}</td><td>종가가 50일·200일선 아래. "(초기)"는 데드크로스 전, "+ 반등 신호"는 MACD 골든크로스 &amp; RSI&gt;45 또는 높은 저점</td></tr>
<tr><td>피벗</td><td>좌우 5거래일 기준 최고/최저 종가, 최근 약 9개월 · RSI 14 · MACD 12/26/9 · 돌파 거래량은 직전 50일 평균 대비</td></tr>
</tbody></table></div>
{final_html}
<footer>데이터: Yahoo Finance(가격), State Street(SPY 보유종목), stockanalysis.com(QQQ·KODEX 200 보유종목) · 일봉 종가 기반 기계적 판정이며 투자 자문이 아닙니다.<br>
{'<br>'.join(html.escape(x) for x in log)}</footer>
</main></body></html>"""
    return today, page


def main():
    os.makedirs(DATA, exist_ok=True)
    log = []
    refresh_holdings(log)
    build_k200_csv(log)
    build_qqq_csv(log)
    lists, res = turn.run_all()
    load_kr_names(res.keys(), log)
    etf_self = {}
    for etf, sym in ETF_SYMBOL.items():
        try:
            etf_self[etf] = turn.analyze(sym)
        except Exception as e:
            log.append(f"{sym} ETF 자체 분석 실패 ({e})")
    today, page = build(lists, res, log, etf_self)
    path = os.path.join(OUT, f"{today}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    shutil.copyfile(path, os.path.join(OUT, "latest.html"))
    with open(os.path.join(DATA, f"{today}.json"), "w", encoding="utf-8") as f:
        json.dump({"lists": lists, "res": res}, f, ensure_ascii=False, default=str)
    errs = [t for t, a in res.items() if "error" in a]
    print(f"[{dt.datetime.now():%F %T}] report: {path} | {len(res)} symbols, errors: {errs or 'none'} | " + " / ".join(log))


if __name__ == "__main__":
    main()
