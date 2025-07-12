# Étape 1: builder
FROM python:3.10-slim-bullseye as builder

RUN apt-get update && apt-get install -y build-essential python3-libtorrent

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --upgrade pip
RUN pip3 install -r requirements.txt

# Étape 2: final
FROM python:3.10-slim-bullseye

RUN apt-get update && apt-get install -y python3-libtorrent redis-server

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY . .

ENV PATH="/usr/local/bin:$PATH"

EXPOSE 8000 6379

CMD redis-server --daemonize yes && gunicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
