import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
