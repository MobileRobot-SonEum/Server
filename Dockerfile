# temi 에이전트 서버 이미지 (CPU만 필요. LLM은 Ollama 컨테이너나 외부 API)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/temi_agent.db

WORKDIR /app

# 의존성 먼저 (코드만 바뀌면 이 층은 캐시 재사용)
COPY pyproject.toml ./
COPY app ./app
RUN pip install -e .

COPY config ./config
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 temi \
    && mkdir -p /data /app/results \
    && chown -R temi /data /app/results
# root로 시작해 마운트 폴더 권한을 맞춘 뒤 temi로 내려간다 (docker/entrypoint.sh)
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
