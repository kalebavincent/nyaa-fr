# main.py
import requests
import feedparser
from fastapi import FastAPI, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from typing import Optional
import math
from datetime import datetime
from urllib.parse import urlencode
import logging
import html

app = FastAPI()

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("torrentflow")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Configuration du flux RSS
RSS_BASE_URL = "https://nyaa.si/"
CATEGORIES = {
    "all": "Toutes catégories",
    "anime": {
        "name": "Anime",
        "subcategories": {
            "all": "Tous",
            "english-translated": "English Translated",
            "non-english-translated": "Non-English Translated",
            "raw": "Raw"
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

def build_rss_url(
    query: Optional[str] = None, 
    category: str = "all", 
    subcategory: str = "all",
    sort: str = "id", 
    order: str = "desc",
    filter: str = "all"
):
    """Construit l'URL du flux RSS avec les paramètres encodés correctement"""
    params = {
        "page": "rss",
    }
    
    # Seuls les paramètres supportés par l'API RSS sont inclus
    if query:
        params["q"] = query
    
    if category != "all":
        if subcategory != "all":
            # Construction de l'ID de catégorie (ex: "1_2" pour Anime English Translated)
            cat_id = ""
            if category == "anime":
                if subcategory == "english-translated":
                    cat_id = "1_2"
                elif subcategory == "non-english-translated":
                    cat_id = "1_3"
                elif subcategory == "raw":
                    cat_id = "1_4"
            elif category == "literature":
                if subcategory == "english-translated":
                    cat_id = "2_2"
                elif subcategory == "non-english-translated":
                    cat_id = "2_3"
                elif subcategory == "raw":
                    cat_id = "2_4"
            elif category == "live_action":
                if subcategory == "english-translated":
                    cat_id = "3_2"
                elif subcategory == "non-english-translated":
                    cat_id = "3_3"
                elif subcategory == "raw":
                    cat_id = "3_4"
            elif category == "software":
                if subcategory == "applications":
                    cat_id = "4_2"
                elif subcategory == "games":
                    cat_id = "4_3"
            
            if cat_id:
                params["c"] = cat_id
        else:
            # Catégorie principale sans sous-catégorie
            if category == "anime":
                params["c"] = "1_1"
            elif category == "literature":
                params["c"] = "2_1"
            elif category == "live_action":
                params["c"] = "3_1"
            elif category == "software":
                params["c"] = "4_1"
    
    if sort in SORT_MAPPING:
        params["s"] = SORT_MAPPING[sort]
    
    if order in ORDER_MAPPING:
        params["o"] = ORDER_MAPPING[order]
    
    if filter in FILTER_OPTIONS and filter != "all":
        params["f"] = filter
    
    return f"{RSS_BASE_URL}?{urlencode(params)}"

def parse_torrent_entry(entry):
    """Parse une entrée RSS en objet torrent avec gestion des erreurs"""
    # Fonction pour extraire les valeurs des balises nyaa:*
    def get_nyaa_value(tag, default=""):
        # Feedparser stocke les namespaces sous forme de clés combinées
        full_tag = f"nyaa_{tag.lower()}"
        return entry.get(full_tag, default)
    
    # Gestion de la date
    pub_date = datetime.now().strftime("%d/%m/%Y %H:%M")
    if hasattr(entry, 'published_parsed'):
        try:
            pub_date = datetime(*entry.published_parsed[:6]).strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass
    
    # Extraction des valeurs
    title = html.unescape(entry.get("title", "Sans titre"))
    size = get_nyaa_value("size", "0 GB")
    seeders = get_nyaa_value("seeders", "0")
    leechers = get_nyaa_value("leechers", "0")
    category = get_nyaa_value("category", "Inconnue")
    infohash = get_nyaa_value("infohash", "")
    
    # Conversion des nombres
    try:
        seeders = int(seeders)
    except (ValueError, TypeError):
        seeders = 0
    
    try:
        leechers = int(leechers)
    except (ValueError, TypeError):
        leechers = 0
    
    # Construction de l'objet torrent
    torrent = {
        "title": title,
        "link": entry.get("link", ""),
        "pub_date": pub_date,
        "size": size,
        "seeders": seeders,
        "leechers": leechers,
        "category": category,
        "infohash": infohash,
        "completed": get_nyaa_value("downloads", "0")
    }
    
    # Construction du lien magnet
    torrent["magnet"] = f"magnet:?xt=urn:btih:{infohash}"
    
    # Conversion de la taille
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
        "dark_mode": request.cookies.get("dark_mode", "true") == "true"
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
        # Construire l'URL du flux RSS avec uniquement les paramètres supportés
        rss_url = build_rss_url(query, category, subcategory, sort, order, filter)
        logger.info(f"Fetching RSS feed: {rss_url}")
        
        # Récupérer et parser le flux RSS
        response = requests.get(rss_url, timeout=10)
        response.raise_for_status()
        
        # Parse le flux en spécifiant l'espace de noms
        feed = feedparser.parse(response.content)
        
        # Vérifier si le flux contient des entrées
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
                "dark_mode": request.cookies.get("dark_mode", "true") == "true"
            })
        
        # Traiter les entrées
        torrents = [parse_torrent_entry(entry) for entry in feed.entries]
        
        # Pagination
        results_per_page = 20
        total_results = len(torrents)
        total_pages = max(1, math.ceil(total_results / results_per_page))
        
        # Ajuster la pagination si nécessaire
        page = max(1, min(page, total_pages))
        start_index = (page - 1) * results_per_page
        end_index = start_index + results_per_page
        paginated_torrents = torrents[start_index:end_index]
        
        # Calcul des pages à afficher
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
                "dark_mode": request.cookies.get("dark_mode", "true") == "true"
            }
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du flux RSS: {str(e)}")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_message": "Impossible de charger les résultats. Veuillez réessayer plus tard.",
            "dark_mode": request.cookies.get("dark_mode", "true") == "true"
        })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)