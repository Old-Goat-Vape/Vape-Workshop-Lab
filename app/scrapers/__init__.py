# This file makes the 'scrapers' directory a Python package.

from .blck_scraper import scrape_vc_flavors

# You can add other scrapers here as you build them
from .clyrolinx_scraper import run_clyrolinx_scraper
from .vc_flavours_scraper import run_vc_scraper
from .vc_recipes_dtl_scraper import run_vc_dtl_scraper