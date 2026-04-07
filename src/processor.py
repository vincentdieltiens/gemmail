"""
Logique de décision : automatique vs validation manuelle.

Règles de priorité :
1. Règle automatique explicite (config.yml ou générée depuis feedbacks)
2. Confiance haute + dossier connu + pas d'action requise → automatique
3. Tout le reste → en_attente (validation via interface web)
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


def get_relevant_feedbacks(sender_email: str, subject: str) -> list:
    """
    Récupère les feedbacks pertinents pour contextualiser le prompt LLM.
    Priorité : même expéditeur, puis même domaine.
    Limite à 5 exemples maximum.
    """
    feedbacks = db.get_feedbacks_for_context(sender_email)
    return feedbacks[:5]


def apply_action(token: str, email_id: str, action: str, dossier_chemin: str | None):
    """Exécute une action sur un email via Graph API."""
    if action == "deplacer" and dossier_chemin:
        dossier = db.get_dossier_by_chemin(dossier_chemin)
        if dossier:
            graph_client.move_to_folder(token, email_id, dossier["id"])
            graph_client.mark_as_read(token, email_id)
        else:
            db.update_email_error(email_id, f"Dossier introuvable : {dossier_chemin}")
            return

    elif action == "marquer_lu":
        graph_client.mark_as_read(token, email_id)

    elif action == "supprimer":
        graph_client.delete_email(token, email_id)

    db.update_email_decided(email_id, dossier_chemin, action)
    db.add_log(email_id, "action_auto", f"Action automatique : {action} → {dossier_chemin}")


def analyze_and_decide(token: str, raw_email: dict, model: str):
    """
    Analyse LLM + décision pour un email déjà inséré en base.
    Appelé par le worker thread.
    """
    email_id = raw_email["id"]
    sender = raw_email.get("from", {}).get("emailAddress", {})
    sender_name = sender.get("name", "")
    sender_email = sender.get("address", "")
    subject = raw_email.get("subject", "(sans objet)")
    body = raw_email.get("body", {}).get("content", raw_email.get("bodyPreview", ""))

    # 1. Règle automatique → court-circuite le LLM
    regle = check_automatic_rule(sender_email)
    if regle:
        apply_action(token, email_id, regle["action"], regle.get("dossier"))
        db.add_log(email_id, "action_auto", f"Règle automatique appliquée : {regle['action']}")
        return

    # 2. Feedbacks pertinents pour le prompt
    feedbacks = get_relevant_feedbacks(sender_email, subject)

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
        analysis = llm_client.analyze_email(email_data, dossiers, descriptions, feedbacks, model)
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


def process_feedback(email_id: str, dossier_corrige: str, token: str):
    """
    Enregistre un feedback sur une action automatique incorrecte.
    Si un expéditeur est corrigé 3 fois vers le même dossier → règle automatique.
    """
    email = db.get_email(email_id)
    if not email:
        return

    sender_email = email["sender_email"]
    dossier_propose = email["llm_dossier_propose"] or email["decision_dossier"]

    db.add_feedback(
        email_id=email_id,
        sender_email=sender_email,
        dossier_propose=dossier_propose,
        dossier_corrige=dossier_corrige,
    )
    db.add_log(email_id, "feedback", f"Correction : {dossier_propose} → {dossier_corrige}")

    # Appliquer la correction sur l'email
    dossier = db.get_dossier_by_chemin(dossier_corrige)
    if dossier:
        graph_client.move_to_folder(token, email_id, dossier["id"])
        graph_client.mark_as_read(token, email_id)
    db.update_email_decided(email_id, dossier_corrige, "deplacer")

    # Générer une règle automatique si l'expéditeur est corrigé 3 fois
    _maybe_create_auto_rule(sender_email, dossier_corrige)


def _maybe_create_auto_rule(sender_email: str, dossier_corrige: str):
    """Crée une règle automatique si le pattern est répété 3 fois."""
    count = db.count_feedbacks_for_sender(sender_email, dossier_corrige)
    if count >= 3:
        existing = check_automatic_rule(sender_email)
        if not existing:
            db.create_regle(sender_email, dossier_corrige, "deplacer")
            db.add_log(None, "action_auto",
                f"Règle auto créée : {sender_email} → {dossier_corrige} (après {count} corrections)")
