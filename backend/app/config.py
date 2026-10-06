"""Paths and settings, read once at import time.

Secrets (the Gemini key) come from the environment or from backend/.env, which is
gitignored so the key never lands in the repository.
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Tiny KEY=VALUE reader; real environment variables always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(BACKEND_DIR / ".env")

DB_PATH = Path(os.environ.get("EXAM_DB", BACKEND_DIR / "exam.db"))
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT_DIR / "data"))
DATASET_DIR = Path(os.environ.get("DATASET_DIR", DATA_DIR / "source"))
INDEX_DIR = DATA_DIR / "index"        # dense vectors for RAG search
MODELS_DIR = DATA_DIR / "models"      # embedding model cache + trained topic model
EMBED_MODEL = os.environ.get("EMBED_MODEL", "minishlab/potion-retrieval-32M")
DATASET_REPO = os.environ.get(
    "DATASET_REPO", "https://github.com/akarohitmishra/repeatermock-subjectwise-db.git"
)
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# Tried in order: if a model is overloaded (429/5xx) or times out before answering,
# the next one takes over. Override with a comma-separated GEMINI_MODELS.
GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODELS", "gemini-3.5-flash,gemini-flash-lite-latest").split(",") if m.strip()]
