FROM docker:cli
LABEL org.opencontainers.image.title="NAS Control Panel" \
      org.opencontainers.image.description="Simple web GUI to connect SMB shares from other NASes/PCs to Docker Compose apps" \
      org.opencontainers.image.version="1.1.0" \
      org.opencontainers.image.licenses="MIT"
RUN apk add --no-cache python3 py3-flask py3-gunicorn su-exec tzdata
WORKDIR /app
COPY app/ /app/
COPY entrypoint.sh /entrypoint.sh
COPY updater.sh /updater.sh
ENV HOME=/tmp PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PANEL_DATA=/data \
    PUID=1000 PGID=1000 APPS_DIRS=/volume1/docker PANEL_PORT=8090
EXPOSE 8090
HEALTHCHECK --interval=60s --timeout=5s CMD [ -f /tmp/is-updater ] || wget -qO /dev/null http://127.0.0.1:${PANEL_PORT}/login || exit 1
ENTRYPOINT ["/entrypoint.sh"]
