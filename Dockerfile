# Étape 1: Builder l'application
FROM python:3.10-slim-bullseye as builder

# Variables d'environnement
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PIP_NO_CACHE_DIR=off

# Mise à jour système et installation des dépendances système
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Installation des dépendances Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --user -r requirements.txt

# Étape 2: Image finale d'exécution
FROM python:3.10-slim-bullseye

# Installation des dépendances système légères
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copie des fichiers de l'application
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .

# Configuration de l'environnement
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
ENV FLASK_APP=app.py
ENV FLASK_ENV=production
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_RUN_PORT=5000

# Exposition du port et commande de démarrage
EXPOSE 5000
CMD ["gunicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000"]