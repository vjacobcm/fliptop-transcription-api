"""Scrape FlipTop YouTube battle URLs for a fixed set of emcees."""

from __future__ import annotations

import sys
from pathlib import Path

# The matchup parser lives in the API package. Running `cd scraper && python -m
# fliptop_scraper` only puts this directory on sys.path.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

__version__ = "0.1.0"
