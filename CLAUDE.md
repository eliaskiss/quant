# quant

ETF·주식 투자 분석 작업 폴더입니다. 2026-11-12 Claude Code 세션에서 진행한 분석 스크립트와 결과, 그리고 매일 생성되는 상승전환 리포트를 담고 있습니다.

## 폴더 구조

| 경로 | 내용 |
|---|---|
| `dca.py` | SPY·SCHD 적립식 시뮬레이션. 매월 1일 100만원, 2021-12~2026-11, 원화 기준, 배당 포함 |
| `dca2.py` | `dca.py`에 QQQ, KODEX 200(069500.KS)을 추가한 버전 |
| `trend10.py` | 10년 안정성 지표: CAGR, 변동성, MDD, Ulcer Index, 로그가격 R², 12개월 보유 시 수익 확률 |
| `mix.py` | 4종목 배분 백테스트. 원화 기준, 연 1회 리밸런싱. 실행 시 `trend10.py`를 exec하므로 이 폴더에서 실행해야 합니다. |
| `turn.py` | 추세 전환 판정 엔진. 대상은 SPY 30, QQQ 30, KODEX 200 50개(`LIMITS`). `run_all()`, `analyze(sym)`, `parse_sa()` 제공 |
| `turn.json` | `turn.py`를 단독 실행할 때 생성됩니다. 다른 코드에서는 읽지 않습니다. |
| `daily_report.py` | 상승전환 HTML 리포트 생성기 |
| `fundamentals.py` | 매수 후보 재무 조회·등급(`get_many`, `grade`). 캐시는 `fund_cache.json` |
| `ai_review.py` | 매수 추천 후보에 대한 Claude 검토(`review`). 결과는 `daily_report/data/ai/YYYY-MM-DD.json` |
| `run_daily.sh` | cron 진입점. 로그는 `logs/daily_report.log`에 쌓입니다. |
| `daily_report/` | `YYYY-MM-DD.html`, `latest.html`, `data/YYYY-MM-DD.json`(판정 스냅샷, 전일 비교용) |
| `holdings/` | 보유종목 원본(`spy.xlsx`, `qqq.html`, `k200_sa.html`), 전체 구성종목 CSV(`qqq_full.csv`, `k200_full.csv`), 한글명 캐시(`kr_names.json`) |

## 매일 리포트

| 항목 | 내용 |
|---|---|
| 스케줄 | crontab `0 7 * * * /home/elias/source/quant/run_daily.sh` (시스템 TZ는 KST) |
| 처리 순서 | 1) 보유종목 파일 갱신(실패하면 기존 파일 유지) 2) `turn.run_all()` 3) HTML과 JSON 저장 4) 직전 JSON과 비교해 신규 전환(NEW)과 이탈 종목 표시 |
| 리포트 섹션 | 시장 요약(SPY·QQQ·KODEX 200·원/달러·원/100엔의 전일 대비 등락과 1개월 그래프, `MARKET`·`fetch_market`) / ETF별 상태 비중 카드 / 상승전환 확인 / 전환 진행 / 전환 임박(기준가까지 5% 이내) / 전체 종목 / 판정 기준 / **ETF별 추세 요약** / **매수 추천 종목** |
| 재무 분석 (`fundamentals.py`) | 매수 추천 후보만 stockanalysis.com의 `/statistics/`(PER, 선행 PER, PBR, PEG, ROE, 영업이익률, FCF 마진, 부채비율, 3년 매출성장 전망, 배당)와 메인 페이지(Sector)를 조회합니다. `fund_cache.json`에 7일 캐시하고, 실패하면 오래된 캐시를 씁니다. 4개 축(가치·수익성·안정성·성장)의 통과 비율로 A/B/C/D 등급을 매기며, 평가 가능 축이 2개 미만이면 N/A입니다. 금융업(Sector가 Financials)은 PBR·ROE 기준을 쓰고 안정성은 평가하지 않습니다. **D등급은 추천에서 제외**하고 "재무 취약"으로 표시하며, 순서는 재무 등급 → 기술 점수입니다. 상세 기준은 `fundamentals.py` docstring에 있습니다. |
| ETF별 추세 요약 (`closing_section`) | ETF 자체 추세(`SPY`, `QQQ`, `069500.KS`를 `turn.analyze`로 판정), 상위 종목 중 상승(전환+유지) 비중과 하락추세 비중(전일 대비 변화), 상위 3종목 상태. 판단은 상승 60% 이상이면 강세, 하락 60% 이상이면 약세, 그 외는 혼조 |
| 매수 추천 (규칙 기반, 구분별 최대 6개) | ① 추세전환 매수: 상승전환 확인 + RSI ≤ 72 + 돌파 기준가 대비 +8% 이내. 손절은 최근 피벗 저점(`last_low`). ② 눌림목 매수: 상승추세 유지 + RSI 40~62 + 50일선 위 0~4%. 손절은 50일선 -3%. ③ 돌파 대기: 전환 진행·반등 중(또는 50일선 회복) + 기준가까지 3% 이내. 진입은 기준가 돌파 시, 손절은 최근 저점. RSI > 75인 추세 종목은 과열 경고로 따로 표시 |
| AI 검토 (`ai_review.py`) | 규칙으로 확정한 추천 후보(구분별 최대 6개)를 Claude Code CLI(`claude -p --model opus --effort medium`, 구독 인증, WebSearch·WebFetch만 허용, `--json-schema` 출력)로 한 번에 보내 종목별 유지/주의/보류, 다가오는 이벤트, 리스크, 코멘트를 받습니다. **후보 선정과 순위는 바꾸지 않고** 추천 표의 "AI 검토 (참고)" 열과 요약 카드로만 표시합니다. 같은 날 후보가 같으면 저장된 결과를 재사용합니다. CLI가 없거나 로그인이 만료됐거나 호출이 실패하면 건너뛰고 리포트는 그대로 생성됩니다. cron에는 PATH에 `~/.local/bin`이 없어 경로를 직접 찾습니다(`CLAUDE_BIN`으로 지정 가능). `ANTHROPIC_API_KEY`가 환경에 있으면 구독 대신 API 과금이 되므로 설정하지 않습니다. 끄려면 `QUANT_AI_REVIEW=0` |
| 수동 실행 | `./run_daily.sh` 또는 `python3 daily_report.py` |
| 분석 대상 수 | `turn.py`의 `LIMITS = {"SPY": 30, "QQQ": 30, "KODEX200": 50}`. 소스가 부족하면 받을 수 있는 만큼만 분석하고, 리포트 상단에 "(요청 N개, 소스 한계)"로 표시합니다. |
| 전체 목록 CSV | `holdings/qqq_full.csv`, `holdings/k200_full.csv`가 있으면 stockanalysis보다 우선 사용합니다. 형식은 헤더 1줄 + `ticker,name,weight(%)`이고, 한국 종목은 6자리 코드입니다. `daily_report.py`가 매일 자동으로 다시 만들며(`build_k200_csv`, `build_qqq_csv`), 갱신에 실패하면 기존 파일을 유지합니다. 수동으로 넣은 파일은 다음 실행에서 덮어써집니다. |
| 종목 링크 | 종목명을 누르면 TradingView 차트(`https://kr.tradingview.com/chart/dt1Wuwdx/?symbol=`)가 새 탭으로 열립니다. 한국 종목은 `KRX:005930` 형식이고, 미국 종목은 Yahoo `exchangeName`을 TradingView 거래소로 바꿉니다(`daily_report.py`의 `TV_EXCH`: NMS → NASDAQ, NYQ → NYSE, PCX → AMEX). `BRK-B`는 `NYSE:BRK.B`로 바꿉니다. |
| 종목 한글명 | `daily_report.py`의 `KR_NAMES`를 먼저 보고, 없으면 네이버 `m.stock.naver.com/api/stock/{code}/basic`에서 받아 `holdings/kr_names.json`에 캐시합니다. |

## 데이터 소스

| 데이터 | 소스 | 비고 |
|---|---|---|
| 가격·배당·환율 | Yahoo Finance chart API `query1.finance.yahoo.com/v8/finance/chart/{sym}` | User-Agent 헤더가 필요합니다. 한국 종목은 `.KS`, 환율은 `KRW=X`입니다. |
| SPY 보유종목 | State Street xlsx `holdings-daily-us-en-spy.xlsx` | 6번째 행부터 데이터입니다. 열은 Name, Ticker, Identifier, SEDOL, Weight 순서입니다. |
| QQQ·SCHD·KODEX 200 보유종목 | `stockanalysis.com/etf/{qqq,schd}/holdings/`, `/quote/krx/069500/holdings/` | 기준일이 표시되지 않습니다. 무료 페이지는 **상위 25개까지만** 제공합니다(`__data.json`도 동일). |
| **KODEX 200 전체 구성종목** | 삼성자산운용 `samsungfund.com/api/v1/kodex/product-pdf/2ETF01.do?gijunYMD=YYYY.MM.DD` | 약 200종목, 한글명(`secNm`), 실제 비중(`ratio`)을 제공합니다. 현금은 `ratio`가 null입니다. |
| **QQQ 전체 구성종목** | Nasdaq `api.nasdaq.com/api/quote/list-type/nasdaq100` | 101종목의 시가총액만 제공합니다. 비중은 stockanalysis 상위 25개의 실제 비중을 쓰고, 나머지는 시가총액 비중을 보정한 추정치입니다. 브라우저 UA가 필요합니다. |
| KODEX 200 요약 | `m.stock.naver.com/api/stock/069500/etfAnalysis` | 상위 10개만 제공됩니다. |
| 안 되는 소스 | Invesco QQQ 다운로드(406), slickcharts(403), 네이버 `entryJongmok`(410), 네이버 `api/etf/{code}/component`(404), exa 검색(API 키 없음) | Invesco·Nasdaq·네이버 소스 탐색은 사용자 승인(2026-11-12) 후 진행했습니다. |
| openpyxl | `~/.local` 사용자 site-packages에 있습니다. | `python3 -I`로 실행하면 import되지 않습니다. |

## 추세 판정 기준 (`turn.py`)

| 판정 | 조건 |
|---|---|
| 상승추세 유지 | 종가 > 50일선 > 200일선, 200일선 상승(20거래일 전 대비), 52주 고점 대비 -10% 이내 |
| 상승전환 확인 | 최근 하락(6개월 내 고점 대비 -15% 이상 또는 최근 3개월 중 200일선 하회) + 높은 저점 + 직전 고점 종가 돌파 + 50일선 위. 돌파 후 기준가의 97% 아래로 밀리면 무효 |
| 전환 진행(골든크로스 대기) | 종가가 50일·200일선 위, 50일선은 200일선 아래 |
| 반등 중 / 조정 중 / 바닥 반등 시도 | 200일선 위에서 고점 미돌파 / 50일선 하회 / 50일선만 회복 |
| 하락추세 | 종가가 50일·200일선 아래. "(초기)"는 데드크로스 전, "+ 반등 신호"는 MACD 상향 & RSI > 45 또는 높은 저점 |
| 피벗 | 좌우 5거래일(`PIV`) 기준, 최근 약 190거래일 |
| 전환 기준가(`conf`) | 미돌파 종목은 가장 최근 피벗 고점, 돌파한 종목은 돌파한 고점 |
| 상장 기간이 짧은 종목 | 200일선 대신 가용 기간 이동평균으로 대체합니다(예: SPCX, 2026-06 상장). |

## 세션 분석 요약 (2026-11-12 기준)

### 1억원 포트폴리오 예시 (중립형)
미국 대형 25 / 나스닥100 15 / 배당성장 10 / 국내 10 / 신흥국 5 / 미국 장기채 10 / 국내채 10 / 금 10 / 현금성 5 (%)

### 적립식 5년 (2021-12~2026-11, 매월 100만원, 총 6,000만원, 원화 기준)

| 종목 | 평가손익 | 배당(세전) | 손익 + 배당 |
|---|---|---|---|
| KODEX 200 | +6,743만원 (+112.4%) | 351만원 | +7,093만원 (+118.2%) |
| QQQ | +3,750만원 (+62.5%) | 121만원 | +3,871만원 (+64.5%) |
| SPY | +2,850만원 (+47.5%) | 246만원 | +3,095만원 (+51.6%) |
| SCHD | +1,998만원 (+33.3%) | 616만원 | +2,614만원 (+43.6%) |

### 10년 안정성 (현지 통화, 배당 재투자)

| 종목 | CAGR | 변동성 | MDD | 로그가격 R² | 비고 |
|---|---|---|---|---|---|
| SPY | 15.2% | 18.1% | -33.7% | 0.958 | 가장 꾸준한 우상향 |
| SCHD | 13.6% | 16.8% | -33.4% | 0.942 | 변동성·Ulcer Index 최저 |
| QQQ | 20.4% | 22.8% | -35.1% | 0.949 | 수익 최고 |
| KODEX 200 | 15.1% | 26.7% | -46.9% | 0.567 | 박스권 후 2025년 급등, 2026-06-22 고점 이후 -43% |

### 4종목 배분 제안
SPY 40 / QQQ 25 / SCHD 20 / KODEX 200 15. 10년 백테스트에서 CAGR 18.4%, 변동성 18.8%, MDD -28.6%였습니다. 성향별로 공격형 35/40/10/15, 안정형 40/10/35/15입니다.

### 보유종목 메모
- 매그니피센트 7이 SPY의 약 32.9%, QQQ의 약 38.8%를 차지합니다.
- SCHD 상위 10개(TXN, PG, HD, KO, AMGN, PEP, QCOM, ABT, CVX, UNH)는 SPY·QQQ 상위 10개와 겹치지 않습니다.
- KODEX 200은 삼성전자와 SK하이닉스 두 종목이 약 59%입니다.

### 기타 논의
| 주제 | 결론 |
|---|---|
| 코카콜라 1년 +38% | 실적 호조와 가이던스 두 차례 상향(Q2, Q3), 신흥국 성장, 달러 약세, 병입 사업 매각, 방어주 로테이션. 2027년에는 매각 영향으로 매출 약 -8% 예고 |
| AI 투자 관점 | 인프라에서 응용(SW·휴머노이드)으로 이동하는 방향에는 동의. 다만 두 단계가 겹치는 기간이 길고, SW는 선별이 필요하며, 상장 주식으로는 매그7 같은 플랫폼을 통한 노출이 현실적 |
| CRWD | 질은 높지만 PS 43배, 선행 PER 162배로 고평가. 비중 3~5% 이내. 2026-07-02 4:1 분할, 2026-12-01 실적 발표 |
| 추세 전환 스냅샷 | AI 반도체·하드웨어(NVDA, AVGO, MU, 장비주, 삼성전자, SK하이닉스)는 하락추세, 헬스케어·금융·필수소비재와 AAPL·AMZN은 상승추세. 상승전환 확인: COST, 셀트리온 |

## 작업 규칙
- 사용자 대상 설명은 한국어로, 표 중심으로 정리합니다.
- 투자 관련 출력에는 투자 자문이 아니라는 점을 명시합니다.
- 가격 데이터가 이상하면(급락, 분할 등) 분할 이벤트와 원시 시계열을 먼저 확인합니다. 예: CRWD 2026-07 4:1 분할, KOSPI 2026-06 고점 이후 급락은 실제 데이터입니다.
- crontab에는 다른 작업(news_agent, stock_agent)이 함께 있습니다. 수정할 때 `crontab -l`로 기존 내용을 보존합니다.
