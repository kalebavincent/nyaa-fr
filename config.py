import os

class Config:
  RSS_BASE_URL = "https://nyaa.si/"
  BASE_URI = os.getenv("BASE_URI", "http://localhost:8000")
  if BASE_URI.endswith('/'):
    BASE_URI = BASE_URI[:-1]
  RESULTS_PER_PAGE = 20
  FEATURED_COUNT = 8




cf = Config()