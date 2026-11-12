#!/bin/bash
# cron: 매일 07:00 상승전환 리포트 생성
cd "$(dirname "$0")" || exit 1
mkdir -p logs
/usr/bin/python3 daily_report.py >> logs/daily_report.log 2>&1
