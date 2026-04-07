import os
from pathlib import Path
from datetime import date

import yaml

CONFIG_PATH = Path("/app/config.yml")


def load() -> dict:
    if not CONFIG_PATH.exists():
        return _defaults()
    with open(CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}
    return {**_defaults(), **data}


def _defaults() -> dict:
    return {
        "polling_interval": 60,
        "ollama_model": os.environ.get("OLLAMA_MODEL", "gemma4:e2b"),
        "start_date": date.today().isoformat(),
        "dossiers": {},
        "regles_automatiques": [],
    }


def get_start_date() -> str:
    return load().get("start_date", date.today().isoformat())


def get_polling_interval() -> int:
    return int(load().get("polling_interval", 60))


def get_ollama_model() -> str:
    return load().get("ollama_model", "gemma4:e2b")


def get_dossiers_descriptions() -> dict:
    """Retourne {chemin_dossier: description} pour le contexte LLM."""
    return load().get("dossiers", {})


def get_regles_automatiques() -> list:
    return load().get("regles_automatiques", [])


def save_dossier_description(chemin: str, description: str):
    data = load()
    if "dossiers" not in data:
        data["dossiers"] = {}
    data["dossiers"][chemin] = description
    _save(data)


def _save(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
