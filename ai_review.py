"""매수 추천 후보에 대한 Claude 검토 (참고 의견).

- 후보 선정·순위는 daily_report.closing_section 의 규칙 그대로. 여기서는 의견만 붙인다.
- Claude Code CLI(`claude -p`, 구독 인증)를 subprocess 로 호출한다. API 키·SDK 불필요.
- Claude 가 웹 검색으로 최근 뉴스·실적 일정을 확인하고 종목별 verdict(유지/주의/보류)와 코멘트를 JSON 으로 돌려준다.
- 결과는 daily_report/data/ai/YYYY-MM-DD.json 에 저장되고, 같은 날 재실행 시 후보가 같으면 재사용한다.
- CLI 가 없거나 로그인이 만료됐거나 호출이 실패하면 None 을 반환하고 리포트는 기존대로 생성된다.
- 끄기: 환경변수 QUANT_AI_REVIEW=0
"""
import datetime as dt
import json
import os
import shutil
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
AI_DIR = os.path.join(BASE, "daily_report", "data", "ai")
MODEL = "opus"  # Claude Code CLI 별칭 (구독 인증 사용)
TIMEOUT = 900  # 초
VERDICTS = ["유지", "주의", "보류"]

SYSTEM = """당신은 개인 투자자의 일일 리포트를 돕는 리서치 보조입니다.
입력으로 받은 매수 후보는 기술적 규칙과 재무 등급으로 이미 선정된 종목입니다. 선정 자체를 다시 하지 말고, 규칙이 볼 수 없는 정성 정보만 검토하세요.

종목마다 WebSearch(필요 시 WebFetch)로 최근 2주 뉴스와 다음 실적 발표일을 확인하고 아래를 판단합니다.
- 가까운 이벤트 리스크: 7영업일 이내 실적 발표, 배당락, 분할, 대형 소송·규제 결정
- 돌파·반등의 원인: 실적·가이던스 상향처럼 지속성 있는 재료인지, 루머·일회성 뉴스인지
- 재무 수치의 맥락: 고PER 이 성장으로 정당화되는지, 저PER 이 가치 함정인지 (입력된 수치만 인용하고 새 숫자를 지어내지 마세요)
- 후보 간 섹터·테마 편중

verdict 기준
- 유지: 규칙 판단을 뒤집을 정성적 악재가 없음
- 주의: 진입은 가능하나 이벤트·뉴스 리스크로 비중 축소나 이벤트 후 진입이 낫다
- 보류: 추세 신호를 무력화할 수 있는 구체적 악재(실적 쇼크, 가이던스 하향, 규제, 회계 이슈 등)가 확인됨

규칙
- 검색으로 확인한 사실과 추정을 구분하세요. 확인하지 못한 내용은 "확인 못함"이라고 쓰세요.
- next_event 는 날짜를 아는 경우 "YYYY-MM-DD 실적 발표" 형식, 모르면 빈 문자열.
- comment 는 한국어 2문장 이내, risk_flags 는 짧은 한국어 구절 0~3개.
- summary 는 후보 전체에 대한 한국어 3문장 이내 총평(섹터 편중, 공통 리스크 포함).
- 입력의 모든 ticker 에 대해 reviews 항목을 정확히 하나씩 만드세요.
- 검색·조회만 하고 파일 작업은 하지 마세요. 결과는 지정된 JSON 스키마로 반환합니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "verdict": {"type": "string", "enum": VERDICTS},
                    "risk_flags": {"type": "array", "items": {"type": "string"}},
                    "next_event": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["ticker", "verdict", "risk_flags", "next_event", "comment"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "reviews"],
    "additionalProperties": False,
}


def _claude_bin():
    """cron 은 PATH 에 ~/.local/bin 이 없으므로 직접 찾는다."""
    return (os.environ.get("CLAUDE_BIN") or shutil.which("claude")
            or next((p for p in (os.path.expanduser("~/.local/bin/claude"), "/usr/local/bin/claude") if os.path.exists(p)), None))


def _call(exe, payload, use_search):
    """Claude Code CLI(-p, 구독 인증)로 호출 -> (결과 dict, usage, model, cost)."""
    cmd = [exe, "-p", "--model", MODEL, "--effort", "medium", "--output-format", "json",
           "--json-schema", json.dumps(SCHEMA, ensure_ascii=False), "--system-prompt", SYSTEM,
           "--strict-mcp-config", "--no-session-persistence"]
    if use_search:
        cmd += ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch"]
    else:
        cmd += ["--tools", ""]
    prompt = "다음 매수 후보를 검토해 주세요.\n\n" + json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    os.makedirs(AI_DIR, exist_ok=True)
    # cwd 를 데이터 폴더로: 프로젝트 CLAUDE.md 등 작업 지시가 섞이지 않게
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd=AI_DIR)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"CLI 출력 해석 실패 (exit {p.returncode}): {(p.stderr or p.stdout).strip()[:200]}")
    if d.get("is_error") or d.get("subtype") != "success" or not isinstance(d.get("structured_output"), dict):
        raise RuntimeError(f"CLI 오류 ({d.get('subtype')}, {d.get('api_error_status')}): {str(d.get('result'))[:200]}")
    u = d.get("usage") or {}
    usage = {"input": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
             "output": u.get("output_tokens", 0)}
    models = list((d.get("modelUsage") or {}).keys())
    return d["structured_output"], usage, ", ".join(models) or MODEL, d.get("total_cost_usd")


def review(candidates, log):
    """candidates: [{ticker, name, ...}] -> {"summary", "reviews": {ticker: {...}}, "model", "ts"} 또는 None."""
    if not candidates:
        return None
    if os.environ.get("QUANT_AI_REVIEW", "1") == "0":
        log.append("AI 검토: 비활성화(QUANT_AI_REVIEW=0)")
        return None
    today = dt.date.today().isoformat()
    path = os.path.join(AI_DIR, f"{today}.json")
    tickers = sorted(c["ticker"] for c in candidates)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            if sorted(cached.get("tickers", [])) == tickers:
                log.append(f"AI 검토: 오늘 결과 재사용 ({len(tickers)}종목)")
                return cached
        except Exception:
            pass
    exe = _claude_bin()
    if not exe:
        log.append("AI 검토: claude CLI 를 찾지 못해 건너뜀 (CLAUDE_BIN 으로 경로 지정 가능)")
        return None

    payload = {"date": today, "candidates": candidates}
    try:
        try:
            data, usage, model, cost = _call(exe, payload, use_search=True)
            searched = True
        except (RuntimeError, subprocess.TimeoutExpired) as e:  # 한 번만 검색 없이 재시도
            log.append(f"AI 검토: 웹 검색 포함 호출 실패, 검색 없이 재시도 ({str(e)[:160]})")
            data, usage, model, cost = _call(exe, payload, use_search=False)
            searched = False
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        log.append(f"AI 검토: 실패, 건너뜀 ({type(e).__name__}: {str(e)[:200]})")
        return None

    reviews = {r["ticker"]: r for r in data.get("reviews", []) if r.get("ticker") in tickers}
    out = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "model": model, "web_search": searched,
           "tickers": tickers, "summary": data.get("summary", ""), "reviews": reviews, "usage": usage, "cost_usd": cost}
    os.makedirs(AI_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    miss = [t for t in tickers if t not in reviews]
    log.append(f"AI 검토: {model} {len(reviews)}/{len(tickers)}종목"
               f"{'' if searched else ' (웹 검색 없음)'} · 토큰 입력 {usage['input']:,} / 출력 {usage['output']:,}"
               + (f" · 누락 {', '.join(miss)}" if miss else ""))
    return out
