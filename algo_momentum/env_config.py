"""Load algo_momentum settings from algo_momentum/.env before Strategy reads os.environ."""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"


def load_dotenv_file() -> Path | None:
    """Load ENV_FILE into os.environ (existing shell vars win). Returns path if found."""
    if not ENV_FILE.is_file():
        return None
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=False)
    except ImportError:
        _load_simple(ENV_FILE)
    return ENV_FILE


def _load_simple(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.split("#", 1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = val
