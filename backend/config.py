"""Loads config.yaml and backend/.env once. Everything that decides reads from here."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")


@lru_cache(maxsize=1)
def get_config() -> dict:
    with open(BACKEND_DIR / "config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def llm_mode() -> str:
    mode = os.getenv("LLM_MODE", "live").strip().lower()
    if mode not in {"live", "record", "replay"}:
        raise ValueError(f"LLM_MODE must be live, record or replay, got {mode!r}")
    return mode


def groq_key() -> str | None:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return key or None
