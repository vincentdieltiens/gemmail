"""
Logique de décision : automatique vs validation manuelle.

Règles :
- Règle automatique explicite (config.yml) → action immédiate
- Confiance haute + dossier existant + pas d'action requise → automatique
- Tout le reste → en_attente (validation via interface web)
"""

import db
import config
import graph_client
import llm_client


def sync_folders(token: str) -> list:
    """Récupère les dossiers depuis Graph API et les synchronise en base."""
    folders = graph_client.get_folders(token)
    from datetime import datetime
    now = datetime.now().isoformat()
    for f in folders:
        db.upsert_dossier({**f, "last_sync": now})
    return folders


def check_automatic_rule(sender_email: str) -> dict | None:
    """Vérifie si une règle automatique s'applique à cet expéditeur."""
    for regle in db.get_regles_actives():
        if regle["expediteur"] and regle["expediteur"].lower() == sender_email.lower():
            return dict(regle)
    return None


def apply_action(token: str, email_id: str, action: str, dossier_chemin: str | None):
    """Exécute une action sur un email via Graph API."""
    if action in ("deplacer", "marquer_lu") and dossier_chemin:
        dossier = db.get_dossier_by_chemin(dossier_chemin)
        if dossier:
            graph_client.move_to_folder(token, email_id, dossier["id"])
            graph_client.mark_as_read(token, email_id)
        else:
            # Dossier inconnu — passer en validation
            db.update_email_error(email_id, f"Dossier introuvable : {dossier_chemin}")
            return

    elif action == "marquer_lu":
        graph_client.mark_as_read(token, email_id)

    elif action == "supprimer":
        graph_client.delete_email(token, email_id)

    db.update_email_decided(email_id, dossier_chemin, action)
    db.add_log(email_id, "action_auto", f"Action automatique : {action} → {dossier_chemin}")


def process_email(token: str, raw_email: dict, model: str):
    """
    Pipeline complet pour un email :
    1. Création immédiate en base (statut en_traitement)
    2. Vérification règle automatique
    3. Analyse LLM
    4. Décision automatique ou mise en attente
    """
    email_id = raw_email["id"]
    sender = raw_email.get("from", {}).get("emailAddress", {})
    sender_name = sender.get("name", "")
    sender_email = sender.get("address", "")
    subject = raw_email.get("subject", "(sans objet)")
    body = raw_email.get("body", {}).get("content", raw_email.get("bodyPreview", ""))
    received_at = raw_email.get("receivedDateTime", "")

    # 1. Insertion immédiate — visible dans l'UI avec statut "en_traitement"
    db.create_email_pending({
        "id": email_id,
        "subject": subject,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "received_at": received_at,
        "body_preview": body[:500],
    })
    db.add_log(email_id, "detection", f"Email détecté : {subject[:80]}")

    # 2. Règle automatique
    regle = check_automatic_rule(sender_email)
    if regle:
        apply_action(token, email_id, regle["action"], regle.get("dossier"))
        db.add_log(email_id, "action_auto", f"Règle automatique appliquée : {regle['action']}")
        return

    # 3. Analyse LLM
    dossiers = db.get_all_dossiers()
    descriptions = config.get_dossiers_descriptions()

    email_data = {
        "sender_name": sender_name,
        "sender_email": sender_email,
        "subject": subject,
        "body_preview": body,
    }

    try:
        analysis = llm_client.analyze_email(email_data, dossiers, descriptions, model)
    except Exception as e:
        db.update_email_error(email_id, str(e))
        db.add_log(email_id, "erreur", f"Erreur LLM : {e}")
        return

    db.update_email_analysis(email_id, {
        "categorie": analysis["dossier_propose"],
        "dossier_propose": analysis["dossier_propose"],
        "nouveau_dossier": 1 if analysis["nouveau_dossier"] else 0,
        "importance": analysis["importance"],
        "action_requise": 1 if analysis["action_requise"] else 0,
        "resume": analysis["resume"],
        "marquer_lu": 1 if analysis["marquer_lu"] else 0,
        "reponse_proposee": analysis.get("reponse_proposee"),
        "confiance": analysis["confiance"],
    })
    db.add_log(email_id, "analyse", f"LLM → {analysis['dossier_propose']} (confiance: {analysis['confiance']})")

    # 4. Action automatique si confiance haute + dossier connu + pas d'action requise
    if (
        analysis["confiance"] == "haute"
        and not analysis["nouveau_dossier"]
        and not analysis["action_requise"]
        and not analysis.get("reponse_proposee")
    ):
        dossier_chemin = analysis["dossier_propose"]
        action = "marquer_lu" if analysis["marquer_lu"] else "deplacer"
        try:
            apply_action(token, email_id, action, dossier_chemin)
        except Exception as e:
            db.add_log(email_id, "erreur", f"Erreur action auto : {e}")
            # Repasser en attente si l'action échoue
            db.update_email_decided(email_id, dossier_chemin, "erreur")
