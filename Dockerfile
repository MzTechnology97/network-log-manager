FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/netlog-manager
RUN apt-get update && apt-get install -y --no-install-recommends zstd curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY templates ./templates
COPY create-admin.py .
COPY docker/worker-entrypoint.sh /usr/local/bin/netlog-worker
RUN chmod 0755 /usr/local/bin/netlog-worker
RUN useradd --system --uid 10001 --home /var/lib/netlog-manager --shell /usr/sbin/nologin netlog && \
    mkdir -p /var/lib/netlog-manager /var/cache/netlog-manager/exports /var/cache/netlog-manager/history && \
    chown -R netlog:netlog /var/lib/netlog-manager /var/cache/netlog-manager
USER netlog
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:8080/ >/dev/null || exit 1
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8080","--workers","1","--proxy-headers"]
