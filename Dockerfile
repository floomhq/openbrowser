FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY ax_browser_broker ./ax_browser_broker
COPY bin ./bin
COPY config/identities.example.json ./config/identities.example.json

RUN pip install --no-cache-dir -e .

ENV OPENBROWSER_BROKER_HOST=0.0.0.0
ENV OPENBROWSER_BROKER_PORT=8767
ENV OPENBROWSER_BROKER_ROOT=/data/openbrowser-broker
ENV OPENBROWSER_BROWSER_POOL_DIR=/data/openbrowser-pool

EXPOSE 8767

CMD ["openbrowser-broker"]
