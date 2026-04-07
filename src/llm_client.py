import os
import json
import re

import requests

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")

ANALYSIS_PROMPT = """Tu es un assistant qui classe des emails professionnels.

Voici les dossiers disponibles dans la boîte mail de l'utilisateur :
{dossiers_context}
{feedbacks_context}
Analyse l'email suivant et réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ou après.

Champs requis :
- dossier_propose : string — chemin exact d'un dossier existant, ou un nouveau nom si aucun ne convient
- nouveau_dossier : true | false — true si le dossier proposé n'existe pas encore
- importance : "haute" | "moyenne" | "basse"
- action_requise : true | false — l'utilisateur doit-il faire quelque chose ?
- resume : string — résumé en 1 phrase max en français
- marquer_lu : true | false — true uniquement si l'email est purement informatif, sans aucune action
- reponse_proposee : string | null — brouillon de réponse si une réponse est attendue, sinon null
- confiance : "haute" | "moyenne" | "basse" — ta confiance dans le classement proposé

Règles importantes :
- Préfère toujours un dossier existant si le sujet correspond, même partiellement
- N'invente pas un nouveau dossier si un existant peut convenir
- Si tu hésites entre plusieurs dossiers, mets confiance = "basse" ou "moyenne"
- Ne marque jamais comme lu un email qui nécessite une action

Email à analyser :
De : {sender_name} <{sender_email}>
Objet : {subject}
Corps :
{body}
"""


def _ollama_generate(prompt: str, model: str) -> str:
    resp = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def _build_dossiers_context(dossiers: list, descriptions: dict) -> str:
    """Construit la liste des dossiers avec descriptions pour le prompt."""
    lines = []
    for d in dossiers:
        chemin = d["chemin"]
        desc = descriptions.get(chemin, "")
        if desc:
            lines.append(f"- {chemin} : {desc}")
        else:
            lines.append(f"- {chemin}")
    return "\n".join(lines) if lines else "(aucun dossier disponible)"


def _build_feedbacks_context(feedbacks: list) -> str:
    if not feedbacks:
        return ""
    lines = ["\nCorrections passées de l'utilisateur (tiens-en compte) :"]
    for f in feedbacks:
        lines.append(f"- Email de {f['sender_email']} : tu avais proposé \"{f['dossier_propose']}\","
                     f" l'utilisateur a corrigé vers \"{f['dossier_corrige']}\"")
    return "\n".join(lines) + "\n"


def analyze_email(email: dict, dossiers: list, descriptions: dict, feedbacks: list, model: str) -> dict:
    dossiers_context = _build_dossiers_context(dossiers, descriptions)
    feedbacks_context = _build_feedbacks_context(feedbacks)

    prompt = ANALYSIS_PROMPT.format(
        dossiers_context=dossiers_context,
        feedbacks_context=feedbacks_context,
        sender_name=email.get("sender_name", ""),
        sender_email=email.get("sender_email", ""),
        subject=email.get("subject", ""),
        body=email.get("body_preview", "")[:3000],
    )

    raw = _ollama_generate(prompt, model)

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Pas de JSON dans la réponse LLM : {raw[:200]}")

    result = json.loads(match.group())

    # Normalisation des champs
    return {
        "dossier_propose": result.get("dossier_propose", ""),
        "nouveau_dossier": bool(result.get("nouveau_dossier", False)),
        "importance": result.get("importance", "basse"),
        "action_requise": bool(result.get("action_requise", False)),
        "resume": result.get("resume", ""),
        "marquer_lu": bool(result.get("marquer_lu", False)),
        "reponse_proposee": result.get("reponse_proposee"),
        "confiance": result.get("confiance", "basse"),
    }


def wait_for_ollama():
    import time
    for i in range(30):
        try:
            resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
            if resp.status_code == 200:
                print(f"Ollama prêt ({OLLAMA_HOST})")
                return
        except requests.ConnectionError:
            pass
        print(f"Attente Ollama... ({i + 1}/30)")
        time.sleep(5)
    raise RuntimeError("Ollama ne répond pas après 150 secondes")


def ensure_model_pulled(model: str):
    resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=10)
    models = [m["name"] for m in resp.json().get("models", [])]
    if not any(model in m for m in models):
        print(f"Téléchargement du modèle {model}...")
        requests.post(
            f"{OLLAMA_HOST}/api/pull",
            json={"name": model},
            timeout=600,
        ).raise_for_status()
        print(f"Modèle {model} prêt.")
    else:
        print(f"Modèle {model} déjà disponible.")
