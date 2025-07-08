import requests
import feedparser
from urllib.parse import quote_plus
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional
import math
from datetime import datetime
from urllib.parse import urlencode
import logging
import html
import os
import re
from config import cf

app = FastAPI()

# Configuration
RSS_BASE_URL = cf.RSS_BASE_URL
BASE_URI = cf.BASE_URI
if BASE_URI.endswith('/'):
    BASE_URI = BASE_URI[:-1]
RESULTS_PER_PAGE = cf.RESULTS_PER_PAGE
FEATURED_COUNT = cf.FEATURED_COUNT

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("torrentflow")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

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

def build_rss_url(
    query: Optional[str] = None,
    category: str = "all",
    subcategory: str = "all",
    sort: str = "id",
    order: str = "desc",
    filter: str = "all"
):
    """Construit l'URL du flux RSS avec toutes les catégories Nyaa."""
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

    return f"{RSS_BASE_URL}?{urlencode(params)}"

def parse_torrent_entry(entry):
    """Parse une entrée RSS en objet torrent avec magnet complet"""
    def get_nyaa_value(tag, default=""):
        full_tag = f"nyaa_{tag.lower()}"
        return entry.get(full_tag, default)

    pub_date = datetime.now().strftime("%d/%m/%Y %H:%M")
    if hasattr(entry, 'published_parsed'):
        try:
            pub_date = datetime(*entry.published_parsed[:6]).strftime("%d/%m/%Y %H:%M")
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

    # 🔥 Trackers principaux Nyaa
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
    torrent["size_value"] = 0
    size_str = torrent["size"]
    try:
        if "GiB" in size_str or "GB" in size_str:
            torrent["size_value"] = float(size_str.split()[0])
        elif "MiB" in size_str or "MB" in size_str:
            torrent["size_value"] = float(size_str.split()[0]) / 1024
        elif "KiB" in size_str or "KB" in size_str:
            torrent["size_value"] = float(size_str.split()[0]) / (1024 * 1024)
    except (ValueError, IndexError):
        pass

    return torrent

def find_torrent_by_infohash(entries, infohash):
    """Recherche un torrent par infohash dans la liste des entrées"""
    for entry in entries:
        # Vérifier l'infohash dans les champs possibles
        entry_infohash = entry.get('nyaa_infohash', '').lower()
        if entry_infohash == infohash.lower():
            return entry

        # Vérifier dans le lien
        link = entry.get('link', '')
        if infohash.lower() in link.lower():
            return entry

    return None

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "categories": CATEGORIES,
        "sort_options": SORT_OPTIONS,
        "filter_options": FILTER_OPTIONS,
        "total_pages": 1,
        "page": 1,
        "query": "",
        "category": "all",
        "subcategory": "all",
        "sort": "id",
        "order": "desc",
        "filter": "all",
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
    page: int = 1
):
    try:
        rss_url = build_rss_url(query, category, subcategory, sort, order, filter)
        logger.info(f"Fetching RSS feed: {rss_url}")

        response = requests.get(rss_url, timeout=10)
        # response.raise_for_status()

        feed = feedparser.parse(response.content)

        if not feed.entries:
            logger.warning("Le flux RSS est vide")
            return templates.TemplateResponse("results.html", {
                "request": request,
                "torrents": [],
                "query": query,
                "category": category,
                "subcategory": subcategory,
                "sort": sort,
                "order": order,
                "filter": filter,
                "page": page,
                "total_results": 0,
                "total_pages": 1,
                "page_range": range(1, 2),
                "categories": CATEGORIES,
                "sort_options": SORT_OPTIONS,
                "filter_options": FILTER_OPTIONS,
                "dark_mode": request.cookies.get("dark_mode", "true") == "true",
                "base_uri": BASE_URI
            })

        torrents = [parse_torrent_entry(entry) for entry in feed.entries]

        total_results = len(torrents)
        total_pages = max(1, math.ceil(total_results / RESULTS_PER_PAGE))

        page = max(1, min(page, total_pages))
        start_index = (page - 1) * RESULTS_PER_PAGE
        end_index = start_index + RESULTS_PER_PAGE
        paginated_torrents = torrents[start_index:end_index]

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
                "page": page,
                "total_results": total_results,
                "total_pages": total_pages,
                "page_range": page_range,
                "categories": CATEGORIES,
                "sort_options": SORT_OPTIONS,
                "filter_options": FILTER_OPTIONS,
                "dark_mode": request.cookies.get("dark_mode", "true") == "true",
                "base_uri": BASE_URI
            }
        )

    except Exception as e:
        logger.error(f"Erreur lors de la récupération du flux RSS: {str(e)}")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_message": "Impossible de charger les résultats. Veuillez réessayer plus tard.",
            "dark_mode": request.cookies.get("dark_mode", "true") == "true",
            "base_uri": BASE_URI
        })

@app.get("/details/{infohash}", response_class=HTMLResponse)
async def torrent_details(request: Request, infohash: str):
    try:
        # Construire une requête pour récupérer le torrent par son infohash
        rss_url = build_rss_url(query=infohash, sort="id", order="desc")
        logger.info(f"Fetching details for infohash: {infohash}, RSS URL: {rss_url}")

        response = requests.get(rss_url, timeout=10)
        response.raise_for_status()

        feed = feedparser.parse(response.content)

        if not feed.entries:
            logger.warning(f"Aucune entrée trouvée pour l'infohash: {infohash}")
            return RedirectResponse("/", status_code=303)

        # Rechercher le torrent spécifique
        torrent_entry = find_torrent_by_infohash(feed.entries, infohash)

        if not torrent_entry:
            logger.warning(f"Torrent introuvable pour l'infohash: {infohash}")
            return RedirectResponse("/", status_code=303)

        torrent = parse_torrent_entry(torrent_entry)
        logger.info(f"Torrent trouvé: {torrent['title']}")

        return templates.TemplateResponse("details.html", {
            "request": request,
            "torrent": torrent,
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)