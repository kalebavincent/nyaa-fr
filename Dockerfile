# Étape 1: Builder l'application
FROM python:3.10-slim-bullseye as builder

# Variables d'environnement
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off

# Mise à jour système et installation des dépendances système pour build libtorrent et autres libs Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
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

# Installation des dépendances Python
WORKDIR /app
COPY requirements.txt .

# Upgrade pip et installation avec pip3
RUN pip3 install --upgrade pip && \
    pip3 install --user -r requirements.txt && \
    # Installation spécifique de python-libtorrent si nécessaire
    pip3 install --user python-libtorrent

# Étape 2: Image finale d'exécution
FROM python:3.10-slim-bullseye

# Variables d'environnement
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:$PATH" \
    PYTHONPATH="/app"

# Installation des dépendances système légères et runtime pour libtorrent et redis
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libboost-system1.74.0 \
    libboost-chrono1.74.0 \
    libboost-random1.74.0 \
    libssl1.1 \
    redis-server \
    && rm -rf /var/lib/apt/lists/*

# Copie des fichiers de l'application
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .

# Exposition des ports
EXPOSE 8000 6379

# Démarrage de Redis en arrière-plan puis de l'application FastAPI avec gunicorn + uvicorn workers
CMD redis-server --daemonize yes && gunicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
