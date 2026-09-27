FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/netlog-manager
RUN apt-get update && apt-get install -y --no-install-recommends zstd curl smbclient && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY create-admin.py .
COPY docker/healthcheck.py /opt/netlog-manager/healthcheck.py
COPY docker/worker-entrypoint.sh /usr/local/bin/netlog-worker
RUN chmod 0755 /usr/local/bin/netlog-worker /opt/netlog-manager/healthcheck.py
RUN useradd --system --uid 10001 --home /var/lib/netlog-manager --shell /usr/sbin/nologin netlog && \
    mkdir -p /var/lib/netlog-manager /var/cache/netlog-manager/exports /var/cache/netlog-manager/history && \
    chown -R netlog:netlog /var/lib/netlog-manager /var/cache/netlog-manager
USER netlog
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 CMD ["python","/opt/netlog-manager/healthcheck.py"]
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8080","--workers","1","--proxy-headers"]
