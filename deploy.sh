#!/bin/bash
# 원격 서버(.env 의 url, username)에 리포트 생성기와 웹 뷰어(7007)를 배포하고
# 매일 07:00(KST) 리포트 생성 cron 을 등록합니다.
#
# 사용법:
#   ./deploy.sh            코드 배포 + 뷰어 재빌드 + cron 등록
#   ./deploy.sh --users    로컬 viewer/config/users.json 으로 서버 계정을 덮어씀
#                          (기본은 서버에 users.json 이 없을 때만 복사)
#   ./deploy.sh --run      배포 후 서버에서 리포트를 한 번 바로 생성
#
# 뷰어 이미지는 로컬에서 빌드해 docker save/load 로 전송합니다(서버에서는 빌드하지 않음).
# 서버 요구사항: ubuntu, SSH 키 접속, sudo(비밀번호 없이). docker·openpyxl 이 없으면 apt 로 설치합니다.
# 서버의 claude CLI 로그인은 직접 해 두어야 합니다(AI 검토용, 없으면 건너뜀).
set -euo pipefail
cd "$(dirname "$0")"

FORCE_USERS=0
RUN_NOW=0
for a in "$@"; do
  case "$a" in
    --users) FORCE_USERS=1 ;;
    --run) RUN_NOW=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $a" >&2; exit 1 ;;
  esac
done

[ -f .env ] || { echo ".env 가 없습니다 (url=, username=)" >&2; exit 1; }
HOST=$(sed -n 's/^url=//p' .env | tr -d '[:space:]')
USER_=$(sed -n 's/^username=//p' .env | tr -d '[:space:]')
[ -n "$HOST" ] && [ -n "$USER_" ] || { echo ".env 에 url/username 이 없습니다" >&2; exit 1; }
REMOTE="$USER_@$HOST"
DIR=quant   # 서버 홈 아래 배포 경로
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 "$REMOTE")

echo "==> 대상: $REMOTE:~/$DIR"
"${SSH[@]}" "mkdir -p ~/$DIR/daily_report ~/$DIR/holdings ~/$DIR/logs ~/$DIR/viewer/config"

echo "==> 코드 동기화"
rsync -az --delete-after \
  --include='/*.py' --include='/run_daily.sh' --include='/CLAUDE.md' \
  --include='/viewer/' --include='/viewer/*.py' \
  --include='/viewer/docker-compose.yml' --include='/viewer/config/' \
  --include='/viewer/config/users.example.json' \
  --exclude='*' \
  --filter='P /daily_report/' --filter='P /holdings/' --filter='P /logs/' \
  --filter='P /viewer/config/users.json' --filter='P /fund_cache.json' --filter='P /turn.json' \
  ./ "$REMOTE:$DIR/"

echo "==> 기존 리포트·보유종목 데이터 복사 (서버에 없는 파일만)"
[ -d daily_report ] && rsync -az --ignore-existing daily_report/ "$REMOTE:$DIR/daily_report/"
[ -d holdings ] && rsync -az --ignore-existing holdings/ "$REMOTE:$DIR/holdings/"

if [ -f viewer/config/users.json ]; then
  if [ "$FORCE_USERS" = 1 ]; then
    echo "==> users.json 덮어쓰기"
    rsync -a --chmod=F600 viewer/config/users.json "$REMOTE:$DIR/viewer/config/users.json"
  else
    rsync -a --chmod=F600 --ignore-existing viewer/config/users.json "$REMOTE:$DIR/viewer/config/users.json"
  fi
fi

echo "==> 서버 패키지 확인 (docker, openpyxl)"
"${SSH[@]}" LC_ALL=C.UTF-8 bash -s <<'REMOTE_PKG'
set -euo pipefail
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  echo "  docker 설치"
  sudo apt-get update -qq || echo "  (apt update 일부 실패, 설치는 계속 시도)"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 >/dev/null
  sudo systemctl enable --now docker
fi
if ! python3 -c 'import openpyxl' 2>/dev/null; then
  echo "  python3-openpyxl 설치"
  sudo apt-get update -qq || echo "  (apt update 일부 실패, 설치는 계속 시도)"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-openpyxl >/dev/null
fi
REMOTE_PKG

# --- 뷰어 이미지: 로컬에서 빌드해 서버로 전송 (서버에서는 빌드하지 않음) ---
IMAGE=quant-report-viewer:latest
RDOCKER='$(docker info >/dev/null 2>&1 && echo docker || echo "sudo docker")'
case "$("${SSH[@]}" uname -m)" in
  x86_64) PLATFORM=linux/amd64 ;;
  aarch64|arm64) PLATFORM=linux/arm64 ;;
  *) echo "지원하지 않는 서버 아키텍처" >&2; exit 1 ;;
esac
echo "==> 이미지 로컬 빌드 ($PLATFORM)"
docker build -q --provenance=false --sbom=false --platform "$PLATFORM" -t "$IMAGE" viewer >/dev/null
LOCAL_ID=$(docker image inspect -f '{{.Id}}' "$IMAGE")
REMOTE_ID=$("${SSH[@]}" "$RDOCKER image inspect -f '{{.Id}}' $IMAGE 2>/dev/null || true")
if [ "$LOCAL_ID" = "$REMOTE_ID" ]; then
  echo "==> 서버 이미지가 최신입니다 (전송 생략)"
else
  echo "==> 이미지 전송 ($(docker image inspect -f '{{.Size}}' "$IMAGE" | awk '{printf "%.0fMB", $1/1048576}'), gzip 압축)"
  docker save "$IMAGE" | gzip -1 | "${SSH[@]}" "gunzip | $RDOCKER load -q"
fi

echo "==> 서버 설정 (뷰어 실행, cron)"
"${SSH[@]}" LC_ALL=C.UTF-8 DIR="$DIR" RUN_NOW="$RUN_NOW" OLD_ID="${REMOTE_ID:-}" NEW_ID="$LOCAL_ID" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
cd ~/"$DIR"
chmod +x run_daily.sh
rm -f viewer/Dockerfile   # 서버에서는 빌드하지 않음
DOCKER=docker
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

# 뷰어 컨테이너는 uid 1000 으로 실행되므로 파일 소유자 확인
[ "$(id -u)" = 1000 ] || echo "  경고: 서버 사용자 uid 가 $(id -u) 입니다. viewer/Dockerfile 의 USER 를 맞춰야 할 수 있습니다."
[ -f viewer/config/users.json ] || echo "  경고: viewer/config/users.json 이 없어 아무도 로그인할 수 없습니다."

# --- 뷰어 ---
(cd viewer && $DOCKER compose up -d --no-build --pull never --remove-orphans 2>&1 | tail -2)
# 컨테이너 교체 후 최신 이미지만 남기고 이전 버전 뷰어 이미지를 모두 삭제
# (라벨 app=quant-report-viewer 또는 이름 quant-report-viewer 인 것만 대상, 다른 프로젝트 이미지는 건드리지 않음)
for id in $( { $DOCKER image ls -aq --no-trunc --filter label=app=quant-report-viewer
               $DOCKER image ls -aq --no-trunc quant-report-viewer
               [ -n "$OLD_ID" ] && echo "$OLD_ID"; } | sort -u ); do
  [ "$id" = "$NEW_ID" ] && continue
  if $DOCKER rmi -f "$id" >/dev/null 2>&1; then
    echo "  이전 이미지 삭제: ${id:7:12}"
  else
    echo "  이전 이미지 삭제 실패(사용 중일 수 있음): ${id:7:12}"
  fi
done
echo "  남은 뷰어 이미지: $($DOCKER image ls --filter label=app=quant-report-viewer --format '{{.Repository}}:{{.Tag}} {{.ID}}' | tr '\n' ' ')"

# --- cron: 07:00 KST 를 서버 시간대로 환산 ---
OFF=$(date +%z)                       # 예: +0000, +0900
SIGN=${OFF:0:1}; OH=$((10#${OFF:1:2})); OM=$((10#${OFF:3:2}))
[ "$SIGN" = "-" ] && { OH=$((-OH)); OM=$((-OM)); }
MIN=$(( (7*60 - 9*60 + OH*60 + OM + 1440) % 1440 ))
CRON="$((MIN % 60)) $((MIN / 60)) * * * $HOME/$DIR/run_daily.sh"
( crontab -l 2>/dev/null | grep -vF "$HOME/$DIR/run_daily.sh" || true; echo "$CRON" ) | crontab -
echo "  cron: $CRON  (서버 TZ $(date +%Z) $OFF → 07:00 KST)"

# --- claude CLI 확인 (AI 검토) ---
if command -v claude >/dev/null || [ -x ~/.local/bin/claude ]; then
  echo "  claude CLI: $(command -v claude || echo ~/.local/bin/claude)"
else
  echo "  경고: claude CLI 가 없어 AI 검토는 건너뜁니다."
fi

if [ "$RUN_NOW" = 1 ]; then
  echo "  리포트 생성 실행 (수 분 소요)"
  ./run_daily.sh
  tail -1 logs/daily_report.log
fi

sleep 2
curl -fsS -o /dev/null -w "  뷰어 로컬 응답: %{http_code}\n" http://127.0.0.1:7007/login || echo "  경고: 뷰어 응답 없음"
REMOTE_SCRIPT

echo "==> 외부 접속 확인: http://$HOST:7007"
curl -sS -m 8 -o /dev/null -w "  HTTP %{http_code}\n" "http://$HOST:7007/login" \
  || echo "  외부에서 접속되지 않습니다. Lightsail 콘솔 > 네트워킹 > 방화벽에서 TCP 7007 을 열어야 합니다."
