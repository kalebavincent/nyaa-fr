FROM python:3.10-slim-bullseye as builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=off

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-libtorrent \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    libboost-system-dev \
    libboost-python-dev \
    libboost-chrono-dev \
    libboost-random-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --upgrade pip
RUN pip3 install -r requirements.txt

FROM python:3.10-slim-bullseye

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/usr/local/bin:$PATH"
ENV PYTHONPATH="/app"

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-libtorrent \
    redis-server \
    libpq5 \
    curl \
    libboost-system1.74.0 \
    libboost-chrono1.74.0 \
    libboost-random1.74.0 \
    libssl1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin/gunicorn /usr/local/bin/gunicorn
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

COPY . .

EXPOSE 8000 6379

CMD redis-server --daemonize yes && gunicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
