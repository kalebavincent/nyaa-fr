import requests
import feedparser
from urllib.parse import quote_plus
from fastapi import FastAPI, Request, Query, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from typing import Optional, Dict, List
import math
from datetime import datetime
from urllib.parse import urlencode
import logging
import html
import os
import re
import redis
import json
import hashlib
import libtorrent as lt
import tempfile
import time
import concurrent.futures
import asyncio
from config import cf, logger

app = FastAPI()

# Configuration
RSS_BASE_URL = cf.RSS_BASE_URL
BASE_URI = cf.BASE_URI
if BASE_URI.endswith('/'):
    BASE_URI = BASE_URI[:-1]
RESULTS_PER_PAGE = cf.RESULTS_PER_PAGE
FEATURED_COUNT = cf.FEATURED_COUNT
REDIS_HOST = cf.REDIS_HOST
REDIS_PORT = cf.REDIS_PORT
REDIS_DB = cf.REDIS_DB
REDIS_PASSWORD = cf.REDIS_PASSWORD
CACHE_TIMEOUT = cf.CACHE_TIMEOUT
ANALYZE_TIMEOUT = cf.ANALYZE_TIMEOUT
MAX_ANALYZE_WORKERS = cf.MAX_ANALYZE_WORKERS

# Initialisation Redis
try:
    r = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5
    )
    r.ping()
    logger.info("Connecté à Redis avec succès")
except redis.RedisError as e:
    logger.error(f"Erreur de connexion à Redis: {str(e)}")
    r = None

# Headers pour contourner les blocages
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
    'DNT': '1'
}

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Gestionnaire de connexions WebSocket
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.analysis_listeners: Dict[str, List[WebSocket]] = {}
        self.search_listeners: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"Client connecté: {client_id}")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            # Retirer des groupes d'écoute
            for key in list(self.analysis_listeners.keys()):
                if client_id in self.analysis_listeners[key]:
                    self.analysis_listeners[key].remove(client_id)
            for key in list(self.search_listeners.keys()):
                if client_id in self.search_listeners[key]:
                    self.search_listeners[key].remove(client_id)
            logger.info(f"Client déconnecté: {client_id}")

    async def send_personal_message(self, message: Dict, client_id: str):
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_json(message)
            except WebSocketDisconnect:
                self.disconnect(client_id)

    async def broadcast(self, message: Dict):
        for client_id, websocket in self.active_connections.items():
            try:
                await websocket.send_json(message)
            except WebSocketDisconnect:
                self.disconnect(client_id)

    def add_analysis_listener(self, infohash: str, websocket: WebSocket, client_id: str):
        if infohash not in self.analysis_listeners:
            self.analysis_listeners[infohash] = []
        if client_id not in self.analysis_listeners[infohash]:
            self.analysis_listeners[infohash].append(client_id)
        logger.info(f"Écouteur ajouté pour {infohash}: {client_id}")

    def add_search_listener(self, search_key: str, websocket: WebSocket, client_id: str):
        if search_key not in self.search_listeners:
            self.search_listeners[search_key] = []
        if client_id not in self.search_listeners[search_key]:
            self.search_listeners[search_key].append(client_id)
        logger.info(f"Écouteur de recherche ajouté pour {search_key}: {client_id}")

    async def notify_analysis_update(self, infohash: str, analysis: Dict):
        if infohash in self.analysis_listeners:
            message = {
                "type": "analysis_update",
                "infohash": infohash,
                "analysis": analysis
            }
            for client_id in self.analysis_listeners[infohash]:
                await self.send_personal_message(message, client_id)

    async def notify_new_torrents(self, search_key: str, torrents: List[Dict]):
        if search_key in self.search_listeners:
            message = {
                "type": "new_torrents",
                "search_key": search_key,
                "torrents": torrents
            }
            for client_id in self.search_listeners[search_key]:
                await self.send_personal_message(message, client_id)

manager = ConnectionManager()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon.ico")

# Configuration des sites
SITES = {
    "nyaa": {
        "name": "Nyaa",
        "base_url": "https://nyaa.si",
        "rss_url": "https://nyaa.si/?page=rss",
        "type": "nyaa",
        "analyze": False
    },
    "subsplease": {
        "name": "SubsPlease",
        "base_url": "https://subsplease.org",
        "rss_url": "https://subsplease.org/rss/",
        "type": "generic",
        "analyze": True
    },
    "animetosho": {
        "name": "AnimeTosho",
        "base_url": "https://animetosho.org",
        "rss_url": "https://feed.animetosho.org/rss2?only_tor=1",
        "type": "animetosho",
        "analyze": True
    }
}

# Configuration des catégories
CATEGORIES = {
    "all": {
        "name": "Toutes catégories",
        "subcategories": {
            "all": "Tous"
        }
    },
    "anime": {
        "name": "Anime",
        "subcategories": {
            "all": "Tous",
            "english-translated": "English Translated",
            "non-english-translated": "Non-English Translated",
            "raw": "Raw"
        }
    },
    "audio": {
        "name": "Audio",
        "subcategories": {
            "all": "Tous",
            "lossless": "Lossless",
            "lossy": "Lossy"
        }
    },
    "literature": {
        "name": "Littérature",
        "subcategories": {
            "all": "Tous",
            "english-translated": "English Translated",
            "non-english-translated": "Non-English Translated",
            "raw": "Raw"
        }
    },
    "live_action": {
        "name": "Live Action",
        "subcategories": {
            "all": "Tous",
            "english-translated": "English Translated",
            "non-english-translated": "Non-English Translated",
            "raw": "Raw"
        }
    },
    "pictures": {
        "name": "Images",
        "subcategories": {
            "all": "Tous",
            "graphics": "Graphics",
            "photos": "Photos"
        }
    },
    "software": {
        "name": "Logiciels",
        "subcategories": {
            "all": "Tous",
            "applications": "Applications",
            "games": "Jeux"
        }
    }
}

SORT_OPTIONS = {
    "id": "Date",
    "size": "Taille",
    "seeders": "Seeders"
}
SORT_MAPPING = {
    "id": "id",
    "size": "size",
    "seeders": "seeders"
}
ORDER_MAPPING = {
    "desc": "desc",
    "asc": "asc"
}

FILTER_OPTIONS = {
    "all": "Tous",
    "trusted": "Trusted Only",
    "remakes": "Remakes Only"
}

LANGUAGES = {
    "english": {"name": "English", "flag": "🇬🇧"},
    "french": {"name": "French", "flag": "🇫🇷"},
    "japanese": {"name": "Japanese", "flag": "🇯🇵"},
    "multi": {"name": "Multi", "flag": "🌐"},
    "spanish": {"name": "Spanish", "flag": "🇪🇸"},
    "german": {"name": "German", "flag": "🇩🇪"},
    "korean": {"name": "Korean", "flag": "🇰🇷"},
    "unknown": {"name": "Unknown", "flag": "❓"}
}

def detect_language(title: str) -> str:
    """Détecte la langue à partir du titre"""
    title_lower = title.lower()
    if "french" in title_lower or "vostfr" in title_lower or "fr" in title_lower:
        return "french"
    elif "english" in title_lower or "eng" in title_lower:
        return "english"
    elif "japanese" in title_lower or "jap" in title_lower or "raw" in title_lower:
        return "japanese"
    elif "multi" in title_lower:
        return "multi"
    elif "spanish" in title_lower or "esp" in title_lower:
        return "spanish"
    elif "german" in title_lower or "ger" in title_lower:
        return "german"
    elif "korean" in title_lower or "kor" in title_lower:
        return "korean"
    return "unknown"

def parse_size(size_str):
    """Convertit une chaîne de taille en valeur numérique en GB"""
    try:
        size_str = size_str.lower()
        if 'gib' in size_str or 'gb' in size_str:
            return float(size_str.replace('gib', '').replace('gb', '').strip())
        elif 'mib' in size_str or 'mb' in size_str:
            return float(size_str.replace('mib', '').replace('mb', '').strip()) / 1024
        elif 'kib' in size_str or 'kb' in size_str:
            return float(size_str.replace('kib', '').replace('kb', '').strip()) / (1024 * 1024)
        elif 'b' in size_str:
            return float(size_str.replace('b', '').strip()) / (1024 * 1024 * 1024)
        return 0
    except (ValueError, TypeError):
        return 0

def format_size(size_bytes):
    """Formatte la taille en octets en chaîne lisible"""
    if size_bytes == 0:
        return "0B"

    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0

    while size_bytes >= 1024 and unit_index < len(units)-1:
        size_bytes /= 1024.0
        unit_index += 1

    return f"{size_bytes:.2f} {units[unit_index]}"

def analyze_torrent(torrent_url: str, is_magnet: bool = False, timeout=10, infohash: str = ""):
    """Analyse un torrent ou magnet pour extraire les métadonnées"""
    cache_key = f"torrent_analysis:{hashlib.md5(torrent_url.encode()).hexdigest() if torrent_url else infohash}"

    # Vérifier le cache Redis
    if r:
        cached = r.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

    try:
        ses = lt.session()
        params = {
            'save_path': tempfile.gettempdir(),
            'storage_mode': lt.storage_mode_t(2),
        }

        start_time = time.time()

        if is_magnet:
            handle = lt.add_magnet_uri(ses, torrent_url, params)
            status = handle.status()

            # Attendre les métadonnées
            while not handle.has_metadata():
                if time.time() - start_time > timeout:
                    raise TimeoutError("Timeout waiting for metadata")
                time.sleep(0.1)
                status = handle.status()
        else:
            # Télécharger le fichier torrent
            response = requests.get(torrent_url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            torrent_data = lt.bdecode(response.content)
            info = lt.torrent_info(torrent_data)
            params['ti'] = info
            handle = ses.add_torrent(params)
            status = handle.status()

        # Attendre les stats des trackers
        while status.num_seeds == -1 and time.time() - start_time < timeout:
            time.sleep(0.5)
            status = handle.status()

        # Préparer le résultat
        analysis = {
            "seeders": max(status.num_seeds, 0),
            "leechers": max(status.num_peers - status.num_seeds, 0),
            "size_bytes": status.total_wanted,
            "size_human": format_size(status.total_wanted),
            "infohash": str(status.info_hash) if status.info_hash else "",
            "success": status.num_seeds >= 0
        }

        # Mettre en cache
        if r and analysis["success"]:
            r.setex(cache_key, 300, json.dumps(analysis))  # Cache 5 minutes

        return analysis
    except Exception as e:
        logger.error(f"Erreur d'analyse torrent: {str(e)}")
        return {
            "seeders": 0,
            "leechers": 0,
            "size_bytes": 0,
            "size_human": "0 GB",
            "infohash": "",
            "success": False
        }
    finally:
        try:
            if 'handle' in locals():
                ses.remove_torrent(handle)
        except:
            pass

def build_rss_url(
    site: Dict,
    query: Optional[str] = None,
    category: str = "all",
    subcategory: str = "all",
    sort: str = "id",
    order: str = "desc",
    filter: str = "all"
):
    """Construit l'URL du flux RSS pour un site spécifique"""
    if site['type'] == 'nyaa':
        return build_nyaa_rss_url(site, query, category, subcategory, sort, order, filter)
    else:
        return build_generic_rss_url(site, query)

def build_nyaa_rss_url(
    site: Dict,
    query: Optional[str] = None,
    category: str = "all",
    subcategory: str = "all",
    sort: str = "id",
    order: str = "desc",
    filter: str = "all"
):
    """Construit l'URL du flux RSS Nyaa"""
    params = {
        "page": "rss",
    }

    if query:
        params["q"] = query

    cat_id = ""

    # Anime (1)
    if category == "anime":
        if subcategory == "english-translated":
            cat_id = "1_2"
        elif subcategory == "non-english-translated":
            cat_id = "1_3"
        elif subcategory == "raw":
            cat_id = "1_4"
        else:
            cat_id = "1_1"

    # Audio (2)
    elif category == "audio":
        if subcategory == "lossless":
            cat_id = "2_2"
        elif subcategory == "lossy":
            cat_id = "2_3"
        else:
            cat_id = "2_1"

    # Literature (3)
    elif category == "literature":
        if subcategory == "english-translated":
            cat_id = "3_2"
        elif subcategory == "non-english-translated":
            cat_id = "3_3"
        elif subcategory == "raw":
            cat_id = "3_4"
        else:
            cat_id = "3_1"

    # Live Action (4)
    elif category == "live_action":
        if subcategory == "english-translated":
            cat_id = "4_2"
        elif subcategory == "non-english-translated":
            cat_id = "4_3"
        elif subcategory == "raw":
            cat_id = "4_4"
        else:
            cat_id = "4_1"

    # Pictures (5)
    elif category == "pictures":
        if subcategory == "graphics":
            cat_id = "5_2"
        elif subcategory == "photos":
            cat_id = "5_3"
        else:
            cat_id = "5_1"

    # Software (6)
    elif category == "software":
        if subcategory == "applications":
            cat_id = "6_2"
        elif subcategory == "games":
            cat_id = "6_3"
        else:
            cat_id = "6_1"

    if cat_id:
        params["c"] = cat_id

    if sort in SORT_MAPPING:
        params["s"] = SORT_MAPPING[sort]

    if order in ORDER_MAPPING:
        params["o"] = ORDER_MAPPING[order]

    if filter in FILTER_OPTIONS and filter != "all":
        params["f"] = filter

    return f"{site['base_url']}?{urlencode(params)}"

def build_generic_rss_url(site: Dict, query: Optional[str] = None):
    """Construit l'URL du flux RSS pour les sites génériques"""
    rss_url = site['rss_url']
    if query:
        if '?' in rss_url:
            rss_url += f"&q={quote_plus(query)}"
        else:
            rss_url += f"?q={quote_plus(query)}"
    return rss_url

def parse_nyaa_entry(entry):
    """Parse une entrée RSS Nyaa"""
    def get_nyaa_value(tag, default=""):
        full_tag = f"nyaa_{tag.lower()}"
        return entry.get(full_tag, default)

    pub_date = datetime.now().strftime("%d/%m/%Y %H:%M")
    pub_timestamp = datetime.now().timestamp()
    if hasattr(entry, 'published_parsed'):
        try:
            pub_date = datetime(*entry.published_parsed[:6]).strftime("%d/%m/%Y %H:%M")
            pub_timestamp = datetime(*entry.published_parsed[:6]).timestamp()
        except Exception:
            pass

    title = html.unescape(entry.get("title", "Sans titre"))
    size = get_nyaa_value("size", "0 GB")
    seeders = get_nyaa_value("seeders", "0")
    leechers = get_nyaa_value("leechers", "0")
    category = get_nyaa_value("category", "Inconnue")
    infohash = get_nyaa_value("infohash", "")

    try:
        seeders = int(seeders)
    except (ValueError, TypeError):
        seeders = 0

    try:
        leechers = int(leechers)
    except (ValueError, TypeError):
        leechers = 0

    # Détection de la langue
    lang_key = detect_language(title)
    language = LANGUAGES.get(lang_key, LANGUAGES["unknown"])

    # Trackers principaux
    trackers = [
        "http://nyaa.tracker.wf:7777/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://exodus.desync.com:6969/announce",
        "udp://tracker.torrent.eu.org:451/announce"
    ]

    # Construction du lien magnet complet
    magnet = f"magnet:?xt=urn:btih:{infohash}&dn={quote_plus(title)}"
    for tr in trackers:
        magnet += f"&tr={quote_plus(tr)}"

    torrent = {
        "title": title,
        "link": entry.get("link", ""),
        "pub_date": pub_date,
        "pub_timestamp": pub_timestamp,
        "size": size,
        "seeders": seeders,
        "leechers": leechers,
        "category": category,
        "infohash": infohash,
        "completed": get_nyaa_value("downloads", "0"),
        "language": language,
        "language_flag": language["flag"],
        "magnet": magnet
    }

    # Calcul size_value
    torrent["size_value"] = parse_size(size)
    return torrent

def parse_generic_entry(entry):
    """Parse une entrée RSS générique"""
    pub_date = datetime.now().strftime("%d/%m/%Y %H:%M")
    pub_timestamp = datetime.now().timestamp()
    if hasattr(entry, 'published_parsed'):
        try:
            pub_date = datetime(*entry.published_parsed[:6]).strftime("%d/%m/%Y %H:%M")
            pub_timestamp = datetime(*entry.published_parsed[:6]).timestamp()
        except Exception:
            pass

    title = html.unescape(entry.get("title", "Sans titre"))

    # Détection de la langue
    lang_key = detect_language(title)
    language = LANGUAGES.get(lang_key, LANGUAGES["unknown"])

    # Extraction du lien magnet
    magnet = ""
    if 'magneturi' in entry:
        magnet = entry['magneturi']
    else:
        for link in entry.get('links', []):
            if link.get('type') == 'application/x-bittorrent' or 'magnet:' in link.get('href', ''):
                magnet = link['href']
                break
        if not magnet:
            magnet = entry.get('link', '')

    # Extraction de l'infohash
    infohash = ""
    if magnet.startswith("magnet:"):
        match = re.search(r'xt=urn:btih:([0-9a-fA-F]{40})', magnet)
        if match:
            infohash = match.group(1).lower()

    # Extraction de la taille depuis la description
    size = "0 GB"
    if 'description' in entry:
        size_match = re.search(r'<strong>Size</strong>: ([\d.]+ (?:GB|MB|KB))', entry.description, re.IGNORECASE)
        if not size_match:
            size_match = re.search(r'Total Size: ([\d.]+ (?:GB|MB|KB))', entry.description, re.IGNORECASE)
        if size_match:
            size = size_match.group(1)

    # Extraction du lien torrent
    torrent_url = ""
    if 'links' in entry:
        for link in entry.get('links', []):
            if link.get('type') == 'application/x-bittorrent':
                torrent_url = link.href
                break

    return {
        "title": title,
        "link": entry.get("link", ""),
        "pub_date": pub_date,
        "pub_timestamp": pub_timestamp,
        "size": size,
        "size_value": parse_size(size),
        "seeders": 0,
        "leechers": 0,
        "category": "Generic",
        "infohash": infohash,
        "completed": 0,
        "language": language,
        "language_flag": language["flag"],
        "magnet": magnet,
        "torrent_url": torrent_url
    }

def parse_animetosho_entry(entry):
    """Parser spécifique pour AnimeTosho"""
    pub_date = datetime.now().strftime("%d/%m/%Y %H:%M")
    pub_timestamp = datetime.now().timestamp()
    if hasattr(entry, 'published_parsed'):
        try:
            pub_date = datetime(*entry.published_parsed[:6]).strftime("%d/%m/%Y %H:%M")
            pub_timestamp = datetime(*entry.published_parsed[:6]).timestamp()
        except Exception:
            pass

    title = html.unescape(entry.get("title", "Sans titre"))

    # Extraction de la taille depuis la description
    size = "0 GB"
    if 'description' in entry:
        size_match = re.search(r'<strong>Total Size</strong>: ([\d.]+ (?:GB|MB|KB))', entry.description, re.IGNORECASE)
        if size_match:
            size = size_match.group(1)

    # Extraction du lien magnet
    magnet = ""
    if 'description' in entry:
        magnet_match = re.search(r'href="(magnet:\?[^"]+)"', entry.description)
        if magnet_match:
            magnet = magnet_match.group(1)

    # Extraction de l'infohash
    infohash = ""
    if magnet:
        match = re.search(r'xt=urn:btih:([0-9a-fA-F]{40})', magnet)
        if match:
            infohash = match.group(1).lower()

    # Extraction du lien torrent
    torrent_url = ""
    if 'links' in entry:
        for link in entry.links:
            if link.get('type') == 'application/x-bittorrent':
                torrent_url = link.href
                break

    # Détection de la langue
    lang_key = detect_language(title)
    language = LANGUAGES.get(lang_key, LANGUAGES["unknown"])

    return {
        "title": title,
        "link": entry.get("link", ""),
        "pub_date": pub_date,
        "pub_timestamp": pub_timestamp,
        "size": size,
        "size_value": parse_size(size),
        "seeders": 0,
        "leechers": 0,
        "category": "Anime",
        "infohash": infohash,
        "completed": 0,
        "language": language,
        "language_flag": language["flag"],
        "magnet": magnet,
        "torrent_url": torrent_url
    }

def parse_entry(entry, site_type: str, site_config: Dict):
    """Dispatch vers le parser approprié"""
    if site_type == "nyaa":
        return parse_nyaa_entry(entry)
    elif site_type == "animetosho":
        return parse_animetosho_entry(entry)
    else:
        return parse_generic_entry(entry)

def get_cache_key(site: Dict, params: Dict) -> str:
    """Génère une clé de cache unique pour la requête"""
    key_data = {
        "site": site['name'],
        "query": params.get("query", ""),
        "category": params.get("category", ""),
        "subcategory": params.get("subcategory", ""),
        "sort": params.get("sort", ""),
        "order": params.get("order", ""),
        "filter": params.get("filter", "")
    }
    key_str = json.dumps(key_data, sort_keys=True)
    return f"torrents:{hashlib.md5(key_str.encode()).hexdigest()}"

def is_site_available(url):
    """Vérifie si un site est accessible"""
    try:
        response = requests.head(url, headers=HEADERS, timeout=5)
        return response.status_code < 400
    except requests.exceptions.RequestException:
        return False

def analyze_torrents(torrents: List[Dict], site_config: Dict):
    """Analyse les torrents en parallèle pour extraire les stats"""
    if not site_config.get("analyze", False):
        return torrents

    logger.info(f"Début de l'analyse pour {len(torrents)} torrents...")

    # Préparer les tâches d'analyse avec validation
    analyze_tasks = []
    for torrent in torrents:
        torrent_url = torrent.get("torrent_url") or torrent.get("magnet")

        # Filtrer les URLs vides ou invalides
        if not torrent_url or not torrent_url.strip():
            logger.warning(f"URL manquante pour le torrent: {torrent.get('title')}")
            continue

        # Vérifier le schéma de l'URL
        if torrent_url.startswith("magnet:") or torrent_url.startswith("http"):
            analyze_tasks.append(torrent)
        else:
            logger.warning(f"URL invalide: {torrent_url}")

    # Fonction pour analyser un seul torrent
    def analyze_single(torrent):
        try:
            torrent_url = torrent.get("torrent_url") or torrent.get("magnet")

            # Vérifier à nouveau l'URL
            if not torrent_url or not torrent_url.strip():
                return torrent

            is_magnet = torrent_url.startswith("magnet:")
            analysis = analyze_torrent(
                torrent_url,
                is_magnet,
                ANALYZE_TIMEOUT,
                torrent.get("infohash", "")
            )

            if analysis and analysis["success"]:
                torrent["seeders"] = analysis["seeders"]
                torrent["leechers"] = analysis["leechers"]

                if analysis["size_bytes"] > 0 and torrent.get("size_value", 0) == 0:
                    torrent["size"] = analysis["size_human"]
                    torrent["size_value"] = analysis["size_bytes"] / (1024 ** 3)

                if analysis["infohash"] and not torrent.get("infohash"):
                    torrent["infohash"] = analysis["infohash"]

                # Notifier les clients intéressés par cette infohash
                asyncio.run(manager.notify_analysis_update(
                    torrent.get("infohash", ""),
                    {
                        "seeders": torrent["seeders"],
                        "leechers": torrent["leechers"],
                        "size": torrent["size"]
                    }
                ))
        except Exception as e:
            logger.error(f"Erreur d'analyse pour {torrent.get('title')}: {str(e)}")
        return torrent

    # Exécuter en parallèle
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_ANALYZE_WORKERS) as executor:
        futures = {executor.submit(analyze_single, torrent): torrent for torrent in analyze_tasks}

        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"Erreur dans le thread d'analyse: {str(e)}")

    logger.info("Analyse des torrents terminée")
    return torrents

def fetch_torrents(site: Dict, params: Dict) -> List[Dict]:
    """Récupère les torrents d'un site avec cache Redis"""
    cache_key = get_cache_key(site, params)

    # Tentative de récupération depuis le cache
    if r:
        cached = r.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

    # Vérifier si le site est accessible
    if not is_site_available(site['base_url']):
        logger.warning(f"Site {site['name']} inaccessible, utilisation du cache")
        return []

    # Construction de l'URL
    rss_url = build_rss_url(
        site,
        query=params.get("query"),
        category=params.get("category", "all"),
        subcategory=params.get("subcategory", "all"),
        sort=params.get("sort", "id"),
        order=params.get("order", "desc"),
        filter=params.get("filter", "all")
    )

    try:
        logger.info(f"Fetching RSS feed: {rss_url}")
        response = requests.get(rss_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.content)

        torrents = [parse_entry(entry, site['type'], site) for entry in feed.entries]

        # Analyse supplémentaire si activée pour ce site
        torrents = analyze_torrents(torrents, site)

        # Mise en cache
        if r and torrents:
            r.setex(cache_key, CACHE_TIMEOUT, json.dumps(torrents))

        return torrents
    except requests.exceptions.RequestException as e:
        logger.warning(f"Erreur réseau pour {site['name']}: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"Erreur inattendue pour {site['name']}: {str(e)}")
        return []

def find_torrent_by_infohash(entries, infohash):
    """Recherche un torrent par infohash dans la liste des entrées"""
    infohash = infohash.lower()
    for entry in entries:
        # Vérifier l'infohash dans les champs possibles
        if 'nyaa_infohash' in entry and entry.nyaa_infohash.lower() == infohash:
            return entry

        # Vérifier dans le lien
        link = entry.get('link', '')
        if infohash in link.lower():
            return entry

        # Vérifier dans le magnet
        magnet = ""
        if 'magneturi' in entry:
            magnet = entry.magneturi
        else:
            for link in entry.get('links', []):
                if link.get('type') == 'application/x-bittorrent' or 'magnet:' in link.get('href', ''):
                    magnet = link.href
                    break

        if infohash in magnet.lower():
            return entry

    return None

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "categories": CATEGORIES,
        "sort_options": SORT_OPTIONS,
        "filter_options": FILTER_OPTIONS,
        "sites": SITES,
        "total_pages": 1,
        "page": 1,
        "query": "",
        "category": "all",
        "subcategory": "all",
        "sort": "id",
        "order": "desc",
        "filter": "all",
        "site": "all",
        "torrents": [],
        "dark_mode": request.cookies.get("dark_mode", "true") == "true",
        "base_uri": BASE_URI
    })

@app.get("/search", response_class=HTMLResponse)
async def search_torrents(
    request: Request,
    query: Optional[str] = Query(None),
    category: str = "all",
    subcategory: str = "all",
    sort: str = "id",
    order: str = "desc",
    filter: str = "all",
    site: str = "all",
    page: int = 1
):
    try:
        # Déterminer les sites à interroger
        if site == "all":
            sites_to_search = list(SITES.values())
        elif site in SITES:
            sites_to_search = [SITES[site]]
        else:
            sites_to_search = [SITES["nyaa"]]

        # Paramètres de recherche
        search_params = {
            "query": query,
            "category": category,
            "subcategory": subcategory,
            "sort": sort,
            "order": order,
            "filter": filter
        }

        # Récupération des torrents de tous les sites sélectionnés
        all_torrents = []
        for site_data in sites_to_search:
            site_torrents = fetch_torrents(site_data, search_params)
            if site_torrents:
                all_torrents.extend(site_torrents)

        # Si aucun résultat n'est trouvé
        if not all_torrents:
            logger.info("Aucun torrent trouvé pour la recherche")
            return templates.TemplateResponse("results.html", {
                "request": request,
                "torrents": [],
                "query": query,
                "category": category,
                "subcategory": subcategory,
                "sort": sort,
                "order": order,
                "filter": filter,
                "site": site,
                "page": page,
                "total_results": 0,
                "total_pages": 1,
                "page_range": range(1, 2),
                "categories": CATEGORIES,
                "sort_options": SORT_OPTIONS,
                "filter_options": FILTER_OPTIONS,
                "sites": SITES,
                "dark_mode": request.cookies.get("dark_mode", "true") == "true",
                "base_uri": BASE_URI
            })

        # Tri des résultats
        reverse_order = (order == "desc")
        if sort == "seeders":
            all_torrents.sort(key=lambda x: x.get("seeders", 0), reverse=reverse_order)
        elif sort == "size":
            all_torrents.sort(key=lambda x: x.get("size_value", 0), reverse=reverse_order)
        else:  # Par défaut: date
            all_torrents.sort(key=lambda x: x.get("pub_timestamp", 0), reverse=reverse_order)

        # Pagination
        total_results = len(all_torrents)
        total_pages = max(1, math.ceil(total_results / RESULTS_PER_PAGE))
        page = max(1, min(page, total_pages))
        start_index = (page - 1) * RESULTS_PER_PAGE
        end_index = start_index + RESULTS_PER_PAGE
        paginated_torrents = all_torrents[start_index:end_index]

        page_range = range(max(1, page-2), min(total_pages+1, page+3))

        return templates.TemplateResponse(
            "results.html",
            {
                "request": request,
                "torrents": paginated_torrents,
                "query": query,
                "category": category,
                "subcategory": subcategory,
                "sort": sort,
                "order": order,
                "filter": filter,
                "site": site,
                "page": page,
                "total_results": total_results,
                "total_pages": total_pages,
                "page_range": page_range,
                "categories": CATEGORIES,
                "sort_options": SORT_OPTIONS,
                "filter_options": FILTER_OPTIONS,
                "sites": SITES,
                "dark_mode": request.cookies.get("dark_mode", "true") == "true",
                "base_uri": BASE_URI
            }
        )

    except Exception as e:
        logger.error(f"Erreur lors de la recherche: {str(e)}")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_message": "Impossible de charger les résultats. Veuillez réessayer plus tard.",
            "dark_mode": request.cookies.get("dark_mode", "true") == "true",
            "base_uri": BASE_URI
        })

@app.get("/details/{infohash}", response_class=HTMLResponse)
async def torrent_details(request: Request, infohash: str):
    try:
        # Recherche du torrent sur tous les sites
        torrent = None
        for site_id, site_data in SITES.items():
            try:
                # Recherche par infohash
                rss_url = build_rss_url(site_data, query=infohash)
                response = requests.get(rss_url, headers=HEADERS, timeout=15)
                feed = feedparser.parse(response.content)

                if feed.entries:
                    torrent_entry = find_torrent_by_infohash(feed.entries, infohash)
                    if torrent_entry:
                        torrent = parse_entry(torrent_entry, site_data['type'], site_data)
                        torrent["site"] = site_id
                        break
            except Exception as e:
                logger.warning(f"Erreur sur le site {site_id}: {str(e)}")
                continue

        if not torrent:
            return RedirectResponse("/", status_code=303)

        # Analyse supplémentaire si nécessaire
        if (torrent.get("seeders", 0) == 0 or torrent.get("size_value", 0) == 0) and \
           (torrent.get("torrent_url") or torrent.get("magnet")):
            try:
                torrent_url = torrent.get("torrent_url", torrent.get("magnet"))
                is_magnet = torrent_url.startswith("magnet:")
                analysis = analyze_torrent(torrent_url, is_magnet, ANALYZE_TIMEOUT, torrent.get("infohash", ""))

                if analysis and analysis["success"]:
                    torrent["seeders"] = analysis.get("seeders", torrent.get("seeders", 0))
                    torrent["leechers"] = analysis.get("leechers", torrent.get("leechers", 0))

                    if analysis["size_bytes"] > 0:
                        torrent["size"] = analysis.get("size_human", torrent.get("size", "0 GB"))
                        torrent["size_value"] = analysis["size_bytes"] / (1024 ** 3)

                    if analysis["infohash"] and not torrent.get("infohash"):
                        torrent["infohash"] = analysis["infohash"]
            except Exception as e:
                logger.error(f"Erreur d'analyse pour les détails: {str(e)}")

        return templates.TemplateResponse("details.html", {
            "request": request,
            "torrent": torrent,
            "categories": CATEGORIES,
            "dark_mode": request.cookies.get("dark_mode", "true") == "true",
            "base_uri": BASE_URI
        })

    except Exception as e:
        logger.error(f"Erreur lors de la récupération des détails: {str(e)}")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_message": "Impossible de charger les détails du torrent.",
            "dark_mode": request.cookies.get("dark_mode", "true") == "true",
            "base_uri": BASE_URI
        })

# Nouveaux endpoints pour les pages supplémentaires
@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
    """Page de toutes les catégories"""
    return templates.TemplateResponse("categories.html", {
        "request": request,
        "categories": CATEGORIES,
        "dark_mode": request.cookies.get("dark_mode", "true") == "true",
        "base_uri": BASE_URI
    })

@app.get("/top", response_class=HTMLResponse)
async def top_torrents_page(request: Request):
    """Page des torrents populaires"""
    # Récupérer les torrents les plus populaires
    top_torrents = []
    for site_data in SITES.values():
        site_torrents = fetch_torrents(site_data, {"sort": "seeders", "order": "desc"})
        if site_torrents:
            top_torrents.extend(site_torrents[:20])  # Prendre les 20 premiers par site

    # Trier par seeders
    top_torrents.sort(key=lambda x: x.get("seeders", 0), reverse=True)

    return templates.TemplateResponse("top_torrents.html", {
        "request": request,
        "top_torrents": top_torrents[:50],  # Limiter à 50 résultats
        "categories": CATEGORIES,
        "dark_mode": request.cookies.get("dark_mode", "true") == "true",
        "base_uri": BASE_URI
    })

@app.get("/apps", response_class=HTMLResponse)
async def applications_page(request: Request):
    """Page des applications"""
    return templates.TemplateResponse("applications.html", {
        "request": request,
        "categories": CATEGORIES,
        "dark_mode": request.cookies.get("dark_mode", "true") == "true",
        "base_uri": BASE_URI
    })

# Pages légales
@app.get("/legal/mentions", response_class=HTMLResponse)
async def legal_mentions(request: Request):
    """Page des mentions légales"""
    return templates.TemplateResponse("mentions_legales.html", {
        "request": request,
        "categories": CATEGORIES,
        "dark_mode": request.cookies.get("dark_mode", "true") == "true",
        "base_uri": BASE_URI
    })

@app.get("/legal/privacy", response_class=HTMLResponse)
async def privacy_policy(request: Request):
    """Page de politique de confidentialité"""
    return templates.TemplateResponse("politique_confidentialite.html", {
        "request": request,
        "categories": CATEGORIES,
        "dark_mode": request.cookies.get("dark_mode", "true") == "true",
        "base_uri": BASE_URI
    })

@app.get("/legal/cookies", response_class=HTMLResponse)
async def cookies_policy(request: Request):
    """Page de préférences cookies"""
    return templates.TemplateResponse("cookies.html", {
        "request": request,
        "categories": CATEGORIES,
        "dark_mode": request.cookies.get("dark_mode", "true") == "true",
        "base_uri": BASE_URI
    })

@app.get("/legal/dmca", response_class=HTMLResponse)
async def dmca_policy(request: Request):
    """Page DMCA"""
    return templates.TemplateResponse("dmca.html", {
        "request": request,
        "categories": CATEGORIES,
        "dark_mode": request.cookies.get("dark_mode", "true") == "true",
        "base_uri": BASE_URI
    })

@app.get("/legal/terms", response_class=HTMLResponse)
async def terms_of_service(request: Request):
    """Page des conditions d'utilisation"""
    return templates.TemplateResponse("conditions_utilisation.html", {
        "request": request,
        "categories": CATEGORIES,
        "dark_mode": request.cookies.get("dark_mode", "true") == "true",
        "base_uri": BASE_URI
    })

# WebSocket pour les mises à jour en temps réel
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)
    try:
        while True:
            data = await websocket.receive_json()
            # Gestion des différents types de messages
            if data["type"] == "register_analysis":
                infohash = data["infohash"]
                manager.add_analysis_listener(infohash, websocket, client_id)

            elif data["type"] == "register_search":
                search_key = data["search_key"]
                manager.add_search_listener(search_key, websocket, client_id)

    except WebSocketDisconnect:
        manager.disconnect(client_id)

# Tâche périodique pour les nouvelles recherches
async def periodic_search_task():
    while True:
        await asyncio.sleep(300)  # Toutes les 5 minutes

        # Recherche des torrents populaires
        search_params = {
            "sort": "seeders",
            "order": "desc",
            "filter": "trusted"
        }

        for site_id, site_data in SITES.items():
            torrents = fetch_torrents(site_data, search_params)
            if torrents:
                # Créer une clé unique pour cette recherche
                search_key = f"top_{site_id}"
                await manager.notify_new_torrents(search_key, torrents[:10])

        logger.info("Mise à jour périodique des torrents populaires envoyée")

# Démarrer la tâche périodique au démarrage de l'app
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(periodic_search_task())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)