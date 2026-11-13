#!/bin/bash
# cron: 매일 07:00(KST) 상승전환 리포트 생성
cd "$(dirname "$0")" || exit 1
# 서버 시간대와 관계없이 리포트 날짜·생성 시각은 KST 기준
export TZ=Asia/Seoul
export PATH="$HOME/.local/bin:$PATH"
mkdir -p logs
/usr/bin/python3 daily_report.py >> logs/daily_report.log 2>&1
