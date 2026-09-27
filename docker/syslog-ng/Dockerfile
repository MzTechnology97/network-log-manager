FROM debian:12-slim
RUN apt-get update && apt-get install -y --no-install-recommends syslog-ng syslog-ng-mod-sql libdbd-mysql mariadb-client ca-certificates python3 tzdata && rm -rf /var/lib/apt/lists/*
COPY syslog-ng.conf /etc/syslog-ng/syslog-ng.conf.template
COPY entrypoint.sh /usr/local/bin/netlog-syslog-entrypoint
RUN chmod 0755 /usr/local/bin/netlog-syslog-entrypoint
ENTRYPOINT ["/usr/local/bin/netlog-syslog-entrypoint"]
