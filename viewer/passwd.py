"""users.json 사용자 추가·비밀번호 변경·삭제.

사용법 (viewer 폴더에서):
  python3 passwd.py 아이디            # 추가 또는 비밀번호 변경 (비밀번호는 입력 프롬프트)
  python3 passwd.py -d 아이디         # 삭제
  python3 passwd.py -f 경로 아이디    # 다른 파일 지정 (기본: ./config/users.json)
서버는 파일이 바뀌면 자동으로 다시 읽으므로 재시작할 필요가 없습니다.
"""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from server import hash_password  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("user")
    ap.add_argument("-d", "--delete", action="store_true")
    ap.add_argument("-f", "--file", default=str(Path(__file__).resolve().parent / "config" / "users.json"))
    a = ap.parse_args()

    path = Path(a.file)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"users": {}}
    users = data.setdefault("users", {})

    if a.delete:
        if users.pop(a.user, None) is None:
            sys.exit(f"없는 사용자: {a.user}")
        msg = f"삭제: {a.user}"
    else:
        pw = getpass.getpass("비밀번호: ")
        if len(pw) < 8:
            sys.exit("비밀번호는 8자 이상이어야 합니다.")
        if pw != getpass.getpass("비밀번호 확인: "):
            sys.exit("비밀번호가 일치하지 않습니다.")
        msg = ("변경" if a.user in users else "추가") + f": {a.user}"
        users[a.user] = hash_password(pw)

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    print(msg)


if __name__ == "__main__":
    main()
