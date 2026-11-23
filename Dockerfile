# Live shows: the page and the daily refresh, on the standard library only.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

LABEL org.opencontainers.image.title="live-shows" \
      org.opencontainers.image.description="Gigs near you by the artists you listen to" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIVE_SHOWS_DB=/data/live-shows.sqlite

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin live-shows \
    && mkdir /data && chown live-shows:live-shows /data

WORKDIR /app
COPY LICENSE ./
COPY live_shows ./live_shows

USER live-shows
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"]

ENTRYPOINT ["python", "-m", "live_shows"]
CMD ["serve"]
