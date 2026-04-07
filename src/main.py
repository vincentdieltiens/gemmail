import time
import sys
import threading
import queue
from datetime import datetime

import db
import config
import graph_client
import llm_client
import processor

# File d'attente entre le thread de polling et le worker LLM
email_queue: queue.Queue = queue.Queue()


def polling_loop(token_holder: dict, since: str, interval: int):
    """Thread 1 : détecte les nouveaux emails et les met en file d'attente."""
    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Polling...")
        try:
            token = graph_client.get_access_token()
            token_holder["token"] = token
        except Exception as e:
            print(f"  Erreur token : {e}")
            time.sleep(interval)
            continue

        try:
            processor.sync_folders(token)
        except Exception as e:
            print(f"  Erreur sync dossiers : {e}")

        try:
            emails = graph_client.get_new_emails(token, since=since)
        except Exception as e:
            print(f"  Erreur récupération emails : {e}")
            db.add_log(None, "erreur", f"Erreur Graph API : {e}")
            time.sleep(interval)
            continue

        new_count = 0
        for email in emails:
            if db.email_exists(email["id"]):
                continue

            # Insertion immédiate en base → visible dans l'UI
            sender = email.get("from", {}).get("emailAddress", {})
            db.create_email_pending({
                "id": email["id"],
                "subject": email.get("subject", "(sans objet)"),
                "sender_name": sender.get("name", ""),
                "sender_email": sender.get("address", ""),
                "received_at": email.get("receivedDateTime", ""),
                "body_preview": email.get("body", {}).get("content", email.get("bodyPreview", ""))[:500],
            })
            db.add_log(email["id"], "detection", f"Email détecté : {email.get('subject', '')[:80]}")

            # Mise en file d'attente pour analyse LLM
            email_queue.put(email)
            new_count += 1

        if new_count:
            print(f"  {new_count} email(s) mis en file d'attente.")
        else:
            print("  Aucun nouvel email.")

        time.sleep(interval)


def worker_loop(token_holder: dict, model: str):
    """Thread 2 : traite les emails un par un via le LLM."""
    while True:
        try:
            email = email_queue.get(timeout=5)
        except queue.Empty:
            continue

        subject = email.get("subject", "(sans objet)")
        print(f"  [LLM] Analyse : {subject[:70]}")
        try:
            token = token_holder["token"]
            processor.analyze_and_decide(token, email, model)
        except Exception as e:
            print(f"  [LLM] Erreur : {e}")
            db.add_log(email["id"], "erreur", str(e))
        finally:
            email_queue.task_done()


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

    # Thread worker LLM (daemon=True → s'arrête avec le process principal)
    worker = threading.Thread(target=worker_loop, args=(token_holder, model), daemon=True)
    worker.start()

    # Boucle de polling dans le thread principal
    polling_loop(token_holder, since, interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArrêt du daemon.")
        sys.exit(0)
