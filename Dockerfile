# IMDBTraktSyncer container image
# Multi-arch: builds natively on linux/arm64 (e.g. Oracle Cloud Ampere A1) and linux/amd64.
# Uses Debian's Chromium + chromedriver, because Google does not publish
# Chrome for Testing builds for Linux ARM64.
ARG BASE_IMAGE=python:3.13-slim-trixie
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# chromium + matching chromedriver from the same Debian package set (versions always match),
# tini as PID 1 to reap zombie browser processes, fonts so pages lay out correctly.
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium chromium-driver tini fonts-liberation ca-certificates \
 && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man

WORKDIR /app
COPY pyproject.toml requirements.txt README.md LICENSE ./
COPY IMDBTraktSyncer ./IMDBTraktSyncer
COPY docker/scheduler.py /usr/local/bin/imdbtraktsyncer-scheduler
RUN pip install . \
 && chmod 0755 /usr/local/bin/imdbtraktsyncer-scheduler \
 && useradd --create-home --uid 1000 --shell /usr/sbin/nologin syncer \
 && mkdir -p /data && chown syncer:syncer /data

ENV IMDBTRAKTSYNCER_DATA_DIR=/data \
    IMDBTRAKTSYNCER_USE_SYSTEM_CHROME=1 \
    IMDBTRAKTSYNCER_CHROME_BINARY=/usr/bin/chromium \
    IMDBTRAKTSYNCER_CHROMEDRIVER=/usr/bin/chromedriver \
    IMDBTRAKTSYNCER_SKIP_VERSION_CHECK=1 \
    HOME=/home/syncer

USER syncer
VOLUME ["/data"]

HEALTHCHECK --interval=30m --timeout=30s --start-period=10m \
  CMD ["python", "/usr/local/bin/imdbtraktsyncer-scheduler", "health"]

ENTRYPOINT ["/usr/bin/tini", "--", "python", "/usr/local/bin/imdbtraktsyncer-scheduler"]
CMD ["run"]
