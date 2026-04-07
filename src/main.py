import time
import sys
from datetime import datetime

import db
import config
import graph_client
import llm_client
import processor


def poll(token_holder: dict, model: str, since: str):
    """Un cycle de polling : récupère et traite les nouveaux emails."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Polling...")

    # Rafraîchir le token si nécessaire
    try:
        token = graph_client.get_access_token()
        token_holder["token"] = token
    except Exception as e:
        print(f"  Erreur token : {e}")
        return

    # Synchroniser les dossiers
    try:
        processor.sync_folders(token)
    except Exception as e:
        print(f"  Erreur sync dossiers : {e}")

    # Récupérer les nouveaux emails
    try:
        emails = graph_client.get_new_emails(token, since=since)
    except Exception as e:
        print(f"  Erreur récupération emails : {e}")
        db.add_log(None, "erreur", f"Erreur Graph API : {e}")
        return

    new_count = 0
    for email in emails:
        if db.email_exists(email["id"]):
            continue
        new_count += 1
        print(f"  → {email.get('subject', '(sans objet)')[:70]}")
        try:
            processor.process_email(token, email, model)
        except Exception as e:
            print(f"    Erreur traitement : {e}")
            db.add_log(email["id"], "erreur", str(e))

    if new_count == 0:
        print("  Aucun nouvel email.")
    else:
        print(f"  {new_count} email(s) traité(s).")


def main():
    print("=== GemMail daemon ===")

    db.init_db()

    cfg = config.load()
    model = cfg["ollama_model"]
    since = cfg["start_date"]
    interval = cfg["polling_interval"]

    print(f"Modèle    : {model}")
    print(f"Depuis    : {since}")
    print(f"Intervalle: {interval}s")

    llm_client.wait_for_ollama()
    llm_client.ensure_model_pulled(model)

    print("Authentification Microsoft...")
    token = graph_client.get_access_token()
    token_holder = {"token": token}
    print("Authentifié.")

    db.add_log(None, "detection", "Daemon démarré")

    # Premier cycle immédiat
    poll(token_holder, model, since)

    # Boucle principale
    last_poll = time.time()
    while True:
        elapsed = time.time() - last_poll
        if elapsed >= interval:
            poll(token_holder, model, since)
            last_poll = time.time()
        time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArrêt du daemon.")
        sys.exit(0)
