"""상승전환 리포트 뷰어 (로그인 필요).

- 리포트 폴더(REPORT_DIR)의 YYYY-MM-DD.html 을 날짜별로 골라 볼 수 있습니다.
- 로그인 정보는 USERS_FILE(JSON)에서 읽습니다. 파일이 바뀌면 다음 로그인 때 다시 읽습니다.
  형식: {"users": {"아이디": "pbkdf2_sha256$반복수$salt(hex)$hash(hex)"}}
  비밀번호 해시는 `python3 passwd.py 아이디`로 만듭니다.
- 세션은 메모리에 보관합니다(컨테이너 재시작 시 다시 로그인).
표준 라이브러리만 사용합니다.
"""
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

REPORT_DIR = Path(os.environ.get("REPORT_DIR", "/reports"))
USERS_FILE = Path(os.environ.get("USERS_FILE", "/config/users.json"))
PORT = int(os.environ.get("PORT", "7007"))
SESSION_TTL = int(os.environ.get("SESSION_TTL_HOURS", "12")) * 3600
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
COOKIE = "qv_session"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 로그인 실패 제한: IP별 10분에 10회
FAIL_WINDOW, FAIL_MAX = 600, 10

_sessions = {}  # token -> (user, expires)
_fails = {}  # ip -> [timestamps]
_lock = threading.Lock()
_users_cache = {"mtime": None, "users": {}}


def load_users():
    try:
        mtime = USERS_FILE.stat().st_mtime
    except OSError:
        return {}
    if mtime != _users_cache["mtime"]:
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            users = data.get("users", {})
            if not isinstance(users, dict):
                raise ValueError("users must be an object")
        except (OSError, ValueError) as e:
            print(f"[viewer] users.json 읽기 실패: {e}", flush=True)
            return _users_cache["users"]
        _users_cache.update(mtime=mtime, users=users)
    return _users_cache["users"]


def hash_password(password, iterations=600_000, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        algo, it, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError):
        return False


# 존재하지 않는 아이디도 같은 시간이 걸리도록 비교용 더미 해시
_DUMMY = hash_password(secrets.token_hex(8))


def check_login(user, password):
    stored = load_users().get(user)
    ok = verify_password(password, stored if isinstance(stored, str) else _DUMMY)
    return ok and isinstance(stored, str)


def report_dates():
    try:
        names = [p.stem for p in REPORT_DIR.glob("*.html")]
    except OSError:
        return []
    return sorted((n for n in names if DATE_RE.match(n)), reverse=True)


PAGE_CSS = """
:root{--bg:#f6f7f9;--fg:#1d2330;--muted:#6b7280;--card:#fff;--line:#e3e6eb;--accent:#2563eb;--err:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e6e8eb;--muted:#9aa3ae;--card:#1d2128;--line:#2c323b;--accent:#60a5fa;--err:#f87171}}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,"Segoe UI","Noto Sans KR","Malgun Gothic",sans-serif}
a{color:var(--accent)}
button,select,input{font:inherit;color:inherit}
"""

LOGIN_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>리포트 로그인</title>
<style>{css}
.box{{max-width:340px;margin:12vh auto;padding:28px;background:var(--card);border:1px solid var(--line);border-radius:12px}}
h1{{font-size:20px;margin:0 0 18px}}label{{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}}
input{{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg)}}
button{{width:100%;margin-top:20px;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}}
.err{{color:var(--err);font-size:13px;margin-top:12px}}</style></head><body>
<form class="box" method="post" action="/login">
<h1>상승전환 리포트</h1>
<label for="u">아이디</label><input id="u" name="username" autocomplete="username" required autofocus>
<label for="p">비밀번호</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<input type="hidden" name="next" value="{next}">
<button type="submit">로그인</button>{error}</form></body></html>"""

INDEX_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>리포트 {date}</title>
<style>{css}
body{{display:flex;flex-direction:column}}
header{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 16px;background:var(--card);border-bottom:1px solid var(--line)}}
header h1{{font-size:16px;margin:0 12px 0 0}}
select,.btn{{padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);text-decoration:none;color:var(--fg)}}
.btn[aria-disabled=true]{{opacity:.4;pointer-events:none}}
.sp{{flex:1}}.who{{color:var(--muted);font-size:13px}}
iframe{{flex:1;width:100%;border:0;background:#fff}}
.empty{{padding:40px 16px;color:var(--muted)}}</style></head><body>
<header><h1>상승전환 리포트</h1>
<a class="btn" href="{prev_href}" aria-disabled="{prev_dis}" title="이전 날짜">◀</a>
<select aria-label="리포트 날짜" onchange="location.href='/?d='+this.value">{options}</select>
<a class="btn" href="{next_href}" aria-disabled="{next_dis}" title="다음 날짜">▶</a>
<a class="btn" href="/r/{date}.html" target="_blank" rel="noopener">새 탭</a>
<span class="sp"></span><span class="who">{user} · 총 {count}개</span>
<form method="post" action="/logout" style="margin:0"><button class="btn" type="submit">로그아웃</button></form>
</header>{body}</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ReportViewer"
    sys_version = ""

    def log_message(self, fmt, *args):
        print(f"[viewer] {self.client_ip()} {fmt % args}", flush=True)

    def client_ip(self):
        return self.client_address[0]

    # ---------- 공통 ----------
    def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, location, headers=None):
        h = {"Location": location}
        h.update(headers or {})
        self.send(303, b"", headers=h)

    def session_user(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        tok = c[COOKIE].value if COOKIE in c else None
        if not tok:
            return None
        with _lock:
            s = _sessions.get(tok)
            if not s:
                return None
            user, exp = s
            if exp < time.time() or user not in load_users():
                _sessions.pop(tok, None)
                return None
        return user

    def cookie_header(self, token, max_age):
        attrs = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
        if COOKIE_SECURE:
            attrs += "; Secure"
        return attrs

    def read_form(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4096:
            return {}
        raw = self.rfile.read(n).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def same_origin(self):
        # SameSite=Strict 쿠키에 더해 POST의 Origin을 확인합니다.
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            return origin is None
        return urlparse(origin).netloc == self.headers.get("Host")

    # ---------- 라우팅 ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/login":
            nxt = parse_qs(url.query).get("next", ["/"])[0]
            return self.login_page(nxt)
        if url.path == "/favicon.ico":
            return self.send(204, b"", "image/x-icon")
        if url.path == "/healthz":
            return self.send(200, "ok", "text/plain")
        user = self.session_user()
        if not user:
            return self.redirect("/login?next=" + quote(self.path, safe=""))
        if url.path == "/":
            return self.index(user, parse_qs(url.query).get("d", [None])[0])
        m = re.fullmatch(r"/r/(\d{4}-\d{2}-\d{2})\.html", url.path)
        if m:
            return self.report(m.group(1))
        self.send(404, "Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        if not self.same_origin():
            print(f"[viewer] 403 Origin 불일치 origin={self.headers.get('Origin')!r} "
                  f"host={self.headers.get('Host')!r}", flush=True)
            return self.send(403, "Forbidden", "text/plain")
        if path == "/login":
            return self.login()
        if path == "/logout":
            return self.logout()
        self.send(404, "Not found", "text/plain")

    # ---------- 화면 ----------
    def login_page(self, nxt="/", error="", code=200):
        err = f'<div class="err">{html.escape(error)}</div>' if error else ""
        self.send(code, LOGIN_HTML.format(css=PAGE_CSS, next=html.escape(safe_next(nxt)), error=err))

    def login(self):
        ip = self.client_ip()
        now = time.time()
        with _lock:
            recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
            _fails[ip] = recent
        if len(recent) >= FAIL_MAX:
            return self.login_page(error="로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요.", code=429)
        form = self.read_form()
        user, pw = form.get("username", "").strip(), form.get("password", "")
        nxt = safe_next(form.get("next", "/"))
        if not (user and pw and check_login(user, pw)):
            with _lock:
                _fails.setdefault(ip, []).append(now)
            print(f"[viewer] 로그인 실패 user={user!r} ip={ip}", flush=True)
            return self.login_page(nxt, "아이디 또는 비밀번호가 올바르지 않습니다.", 401)
        tok = secrets.token_urlsafe(32)
        with _lock:
            _fails.pop(ip, None)
            for k in [k for k, (_, e) in _sessions.items() if e < now]:
                del _sessions[k]
            _sessions[tok] = (user, now + SESSION_TTL)
        print(f"[viewer] 로그인 user={user!r} ip={ip}", flush=True)
        self.redirect(nxt, {"Set-Cookie": self.cookie_header(tok, SESSION_TTL)})

    def logout(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if COOKIE in c:
            with _lock:
                _sessions.pop(c[COOKIE].value, None)
        self.redirect("/login", {"Set-Cookie": self.cookie_header("", 0)})

    def index(self, user, date):
        dates = report_dates()
        if not dates:
            body = '<div class="empty">아직 생성된 리포트가 없습니다.</div>'
            return self.send(200, INDEX_HTML.format(
                css=PAGE_CSS, date="", prev_href="#", prev_dis="true", next_href="#", next_dis="true",
                options="", user=html.escape(user), count=0, body=body))
        if date not in dates:
            date = dates[0]
        i = dates.index(date)
        older = dates[i + 1] if i + 1 < len(dates) else None
        newer = dates[i - 1] if i > 0 else None
        options = "".join(
            f'<option value="{d}"{" selected" if d == date else ""}>{d}{" (최신)" if j == 0 else ""}</option>'
            for j, d in enumerate(dates))
        body = f'<iframe src="/r/{date}.html" title="리포트 {date}"></iframe>'
        self.send(200, INDEX_HTML.format(
            css=PAGE_CSS, date=date, options=options, user=html.escape(user), count=len(dates), body=body,
            prev_href=f"/?d={older}" if older else "#", prev_dis="false" if older else "true",
            next_href=f"/?d={newer}" if newer else "#", next_dis="false" if newer else "true"))

    def report(self, date):
        p = REPORT_DIR / f"{date}.html"
        try:
            data = p.read_bytes()
        except OSError:
            return self.send(404, "리포트가 없습니다.", "text/plain; charset=utf-8")
        # 리포트 안의 외부 링크는 새 탭으로 열리고, 스크립트는 실행하지 않습니다.
        self.send(200, data, headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                                       "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"})


def safe_next(nxt):
    # 오픈 리다이렉트 방지: 같은 사이트의 경로만 허용
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt or nxt.startswith("/login"):
        return "/"
    return nxt


def main():
    if not load_users():
        print(f"[viewer] 경고: {USERS_FILE}에 사용자가 없습니다. 아무도 로그인할 수 없습니다.", flush=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[viewer] http://0.0.0.0:{PORT}  reports={REPORT_DIR}  users={USERS_FILE}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
