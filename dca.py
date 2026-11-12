import json, urllib.request, datetime as dt

UA = {"User-Agent": "Mozilla/5.0"}
START = dt.date(2021, 12, 1)   # 60 months: 2021-12 ~ 2026-11
END = dt.date(2026, 11, 12)
MONTHLY_KRW = 1_000_000


def fetch(sym):
    p1 = int(dt.datetime(2021, 11, 1).timestamp())
    p2 = int(dt.datetime(2026, 11, 13).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1={p1}&period2={p2}&interval=1d&events=div")
    r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA)))["chart"]["result"][0]
    days = [dt.datetime.utcfromtimestamp(t).date() for t in r["timestamp"]]
    close = r["indicators"]["quote"][0]["close"]  # 분할 반영, 배당 미반영 종가
    px = [(d, c) for d, c in zip(days, close) if c is not None]
    divs = sorted((dt.datetime.utcfromtimestamp(int(k)).date(), v["amount"])
                  for k, v in r.get("events", {}).get("dividends", {}).items())
    splits = r.get("events", {}).get("splits", {})
    return px, divs, splits


def first_on_or_after(series, d):
    for day, v in series:
        if day >= d:
            return day, v
    return None


def last_on_or_before(series, d):
    out = None
    for day, v in series:
        if day <= d:
            out = (day, v)
    return out


fx, _, _ = fetch("KRW=X")
months = []
d = START
while d <= END:
    months.append(d)
    d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)

for sym in ["SPY", "SCHD"]:
    px, divs, splits = fetch(sym)
    lots = []  # (date, shares)
    invested = 0
    for m in months:
        day, price = first_on_or_after(px, m)
        _, rate = last_on_or_before(fx, day)
        usd = MONTHLY_KRW / rate
        lots.append((day, usd / price))
        invested += MONTHLY_KRW
    # 배당: 배당락일 전날까지 보유한 주식 수 기준, 지급 당시 환율로 원화 환산
    div_usd = 0.0
    div_krw = 0.0
    for dd, amt in divs:
        sh = sum(s for day, s in lots if day < dd)
        if sh == 0:
            continue
        _, rate = last_on_or_before(fx, dd)
        div_usd += sh * amt
        div_krw += sh * amt * rate
    shares = sum(s for _, s in lots)
    lday, lpx = last_on_or_before(px, END)
    _, lrate = last_on_or_before(fx, lday)
    value_krw = shares * lpx * lrate
    pnl = value_krw - invested
    print(f"== {sym} ==  (splits: {splits or 'none'})")
    print(f"buys: {len(months)}  first {lots[0][0]}  last {lots[-1][0]}")
    print(f"shares {shares:.4f}  last px {lpx:.2f} on {lday}  fx {lrate:.2f}")
    print(f"invested {invested:,.0f}  value {value_krw:,.0f}  pnl {pnl:,.0f} ({pnl/invested*100:.2f}%)")
    print(f"dividends(gross) ${div_usd:,.2f}  ≈ {div_krw:,.0f} KRW  after15% {div_krw*0.85:,.0f}")
    print(f"pnl+div(gross) {pnl+div_krw:,.0f} ({(pnl+div_krw)/invested*100:.2f}%)  "
          f"pnl+div(net) {pnl+div_krw*0.85:,.0f}")
    print(f"USD basis: invested ${sum(MONTHLY_KRW/last_on_or_before(fx, first_on_or_after(px,m)[0])[1] for m in months):,.0f}  value ${shares*lpx:,.0f}")
