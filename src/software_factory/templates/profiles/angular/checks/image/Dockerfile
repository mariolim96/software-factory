# Node alla versione del progetto più Chromium, per la build e per Karma (ChromeHeadless).
ARG NODE_VERSION=22
FROM node:${NODE_VERSION}-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-liberation \
 && rm -rf /var/lib/apt/lists/* \
 && printf '#!/bin/sh\nexec /usr/bin/chromium --no-sandbox --disable-dev-shm-usage "$@"\n' > /usr/local/bin/chrome-ci \
 && chmod +x /usr/local/bin/chrome-ci

# Karma lancia CHROME_BIN: il wrapper aggiunge --no-sandbox, necessario in un container senza privilegi.
ENV CHROME_BIN=/usr/local/bin/chrome-ci
