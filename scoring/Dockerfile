# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.14.6

FROM python:${PYTHON_VERSION}-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --create-home app \
    && python -m venv /opt/venv


FROM base AS builder

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install -r requirements.txt


FROM base AS runtime

COPY --from=builder /opt/venv /opt/venv

COPY batch ./batch
COPY core ./core
COPY ranking ./ranking
COPY search ./search
COPY webapp ./webapp
COPY main.py .

RUN mkdir -p /app/web_data/uploads /app/web_data/results /app/results \
    && chown -R app:app /app /opt/venv

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/metrics?count=0', timeout=3).read()" || exit 1

CMD ["python", "-m", "webapp.server", "--host", "0.0.0.0", "--port", "8000"]
