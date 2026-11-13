FROM python:3.12-slim
# deploy.sh 가 서버의 이전 버전 이미지를 찾아 지울 때 사용
LABEL app=quant-report-viewer

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    REPORT_DIR=/reports USERS_FILE=/config/users.json PORT=7007

WORKDIR /app
COPY server.py passwd.py ./

USER 1000:1000
EXPOSE 7007
HEALTHCHECK --interval=60s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:7007/healthz')"
CMD ["python3", "server.py"]
