FROM debian:12-slim
RUN apt-get update && apt-get install -y --no-install-recommends syslog-ng syslog-ng-mod-sql libdbd-mysql ca-certificates && rm -rf /var/lib/apt/lists/*
COPY syslog-ng.conf /etc/syslog-ng/syslog-ng.conf
CMD ["syslog-ng","-F","--no-caps"]
