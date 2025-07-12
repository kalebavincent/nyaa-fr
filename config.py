import os
import logging
from logging.handlers import RotatingFileHandler

class Config:
    # Configuration de base
    RSS_BASE_URL = "https://nyaa.si/"
    BASE_URI = os.getenv("BASE_URI", "https://verbose-telegram-9759j66qq67rhj7q-8000.app.github.dev")
    if BASE_URI.endswith('/'):
        BASE_URI = BASE_URI[:-1]
    RESULTS_PER_PAGE = int(os.getenv("RESULTS_PER_PAGE", 20))
    FEATURED_COUNT = int(os.getenv("FEATURED_COUNT", 8))

    # Configuration Redis
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    REDIS_DB = int(os.getenv("REDIS_DB", 0))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
    CACHE_TIMEOUT = int(os.getenv("CACHE_TIMEOUT", 3600))  # 1 heure

    # Configuration analyse torrent
    ANALYZE_TIMEOUT = int(os.getenv("ANALYZE_TIMEOUT", 10))  # secondes
    MAX_ANALYZE_WORKERS = int(os.getenv("MAX_ANALYZE_WORKERS", 5))

    # Configuration du logger
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FORMAT = os.getenv("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    LOG_FILE = os.getenv("LOG_FILE", "var/log/torrentflow/app.log")
    LOG_MAX_SIZE = int(os.getenv("LOG_MAX_SIZE", 10))  # en Mo
    LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 5))

    @staticmethod
    def setup_logger():
        """Configure le logger global"""
        logger = logging.getLogger("torrentflow")
        logger.setLevel(Config.LOG_LEVEL)

        # Créer formateur
        formatter = logging.Formatter(Config.LOG_FORMAT)

        # Handler console
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # Handler fichier (rotation)
        if Config.LOG_FILE:
            os.makedirs(os.path.dirname(Config.LOG_FILE), exist_ok=True)
            file_handler = RotatingFileHandler(
                Config.LOG_FILE,
                maxBytes=Config.LOG_MAX_SIZE * 1024 * 1024,
                backupCount=Config.LOG_BACKUP_COUNT
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        return logger

cf = Config()
logger = cf.setup_logger()