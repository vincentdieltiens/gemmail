import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path("/app/data/gemmail.db")


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # lecture/écriture simultanée daemon + web
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY,
                subject TEXT,
                sender_name TEXT,
                sender_email TEXT,
                received_at TEXT,
                body_preview TEXT,
                statut TEXT DEFAULT 'en_traitement',
                -- statut: en_traitement | en_attente | automatique | traite | erreur

                -- résultat LLM
                llm_categorie TEXT,
                llm_dossier_propose TEXT,
                llm_nouveau_dossier INTEGER DEFAULT 0,
                llm_importance TEXT,
                llm_action_requise INTEGER DEFAULT 0,
                llm_resume TEXT,
                llm_marquer_lu INTEGER DEFAULT 0,
                llm_reponse_proposee TEXT,
                llm_confiance TEXT,
                -- confiance: haute | moyenne | basse

                -- décision finale
                decision_dossier TEXT,
                decision_action TEXT,
                -- decision_action: deplacer | marquer_lu | supprimer | repondre | ignorer

                processed_at TEXT,
                decided_at TEXT
            );

            CREATE TABLE IF NOT EXISTS dossiers (
                id TEXT PRIMARY KEY,
                nom TEXT NOT NULL,
                chemin TEXT NOT NULL,
                description TEXT DEFAULT '',
                parent_id TEXT,
                last_sync TEXT
            );

            CREATE TABLE IF NOT EXISTS regles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expediteur TEXT,
                dossier TEXT,
                action TEXT,
                -- action: deplacer | marquer_lu
                actif INTEGER DEFAULT 1,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id TEXT,
                type TEXT,
                -- type: detection | analyse | action_auto | action_manuelle | erreur | feedback
                message TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS feedbacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id TEXT,
                sender_email TEXT,
                dossier_propose TEXT,
                dossier_corrige TEXT,
                created_at TEXT
            );
        """)


# --- Emails ---

def email_exists(email_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM emails WHERE id = ?", (email_id,)).fetchone()
        return row is not None


def create_email_pending(data: dict):
    """Insère l'email dès détection, avant analyse LLM."""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO emails
            (id, subject, sender_name, sender_email, received_at, body_preview, statut)
            VALUES (:id, :subject, :sender_name, :sender_email, :received_at, :body_preview, 'en_traitement')
        """, data)


def update_email_analysis(email_id: str, analysis: dict):
    """Met à jour l'email avec le résultat de l'analyse LLM."""
    statut = "automatique" if analysis.get("confiance") == "haute" and not analysis.get("nouveau_dossier") else "en_attente"
    with get_connection() as conn:
        conn.execute("""
            UPDATE emails SET
                statut = :statut,
                llm_categorie = :categorie,
                llm_dossier_propose = :dossier_propose,
                llm_nouveau_dossier = :nouveau_dossier,
                llm_importance = :importance,
                llm_action_requise = :action_requise,
                llm_resume = :resume,
                llm_marquer_lu = :marquer_lu,
                llm_reponse_proposee = :reponse_proposee,
                llm_confiance = :confiance,
                processed_at = :processed_at
            WHERE id = :email_id
        """, {**analysis, "statut": statut, "email_id": email_id, "processed_at": datetime.now().isoformat()})


def update_email_error(email_id: str, message: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE emails SET statut = 'erreur', processed_at = ? WHERE id = ?",
            (datetime.now().isoformat(), email_id)
        )


def update_email_decided(email_id: str, dossier: str, action: str):
    with get_connection() as conn:
        conn.execute("""
            UPDATE emails SET
                statut = 'traite',
                decision_dossier = ?,
                decision_action = ?,
                decided_at = ?
            WHERE id = ?
        """, (dossier, action, datetime.now().isoformat(), email_id))


def get_emails_by_statut(statut: str) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM emails WHERE statut = ? ORDER BY received_at DESC",
            (statut,)
        ).fetchall()


def get_all_emails(limit: int = 100, offset: int = 0) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM emails ORDER BY received_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()


def get_email(email_id: str):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()


def count_emails_by_statut() -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT statut, COUNT(*) as n FROM emails GROUP BY statut"
        ).fetchall()
        return {row["statut"]: row["n"] for row in rows}


# --- Dossiers ---

def upsert_dossier(data: dict):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO dossiers (id, nom, chemin, parent_id, last_sync)
            VALUES (:id, :nom, :chemin, :parent_id, :last_sync)
            ON CONFLICT(id) DO UPDATE SET
                nom = excluded.nom,
                chemin = excluded.chemin,
                parent_id = excluded.parent_id,
                last_sync = excluded.last_sync
        """, data)


def update_dossier_description(dossier_id: str, description: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE dossiers SET description = ? WHERE id = ?",
            (description, dossier_id)
        )


def get_all_dossiers() -> list:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM dossiers ORDER BY chemin").fetchall()


def get_dossier_by_chemin(chemin: str):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM dossiers WHERE chemin = ?", (chemin,)).fetchone()


# --- Règles automatiques ---

def get_regles_actives() -> list:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM regles WHERE actif = 1").fetchall()


def create_regle(expediteur: str, dossier: str, action: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO regles (expediteur, dossier, action, created_at)
            VALUES (?, ?, ?, ?)
        """, (expediteur, dossier, action, datetime.now().isoformat()))


# --- Logs ---

def add_log(email_id: str | None, type_: str, message: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO logs (email_id, type, message, created_at)
            VALUES (?, ?, ?, ?)
        """, (email_id, type_, message, datetime.now().isoformat()))


def get_logs(limit: int = 200, offset: int = 0) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM logs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()


# --- Feedbacks ---

def add_feedback(email_id: str, sender_email: str, dossier_propose: str, dossier_corrige: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO feedbacks (email_id, sender_email, dossier_propose, dossier_corrige, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (email_id, sender_email, dossier_propose, dossier_corrige, datetime.now().isoformat()))


def get_feedbacks_for_context(sender_email: str) -> list:
    """Retourne les feedbacks pertinents : même expéditeur ou même domaine."""
    domain = sender_email.split("@")[-1] if "@" in sender_email else ""
    with get_connection() as conn:
        # Même expéditeur en priorité, puis même domaine
        return conn.execute("""
            SELECT * FROM feedbacks
            WHERE sender_email = ?
               OR sender_email LIKE ?
            ORDER BY
                CASE WHEN sender_email = ? THEN 0 ELSE 1 END,
                created_at DESC
            LIMIT 5
        """, (sender_email, f"%@{domain}", sender_email)).fetchall()


def count_feedbacks_for_sender(sender_email: str, dossier_corrige: str) -> int:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as n FROM feedbacks
            WHERE sender_email = ? AND dossier_corrige = ?
        """, (sender_email, dossier_corrige)).fetchone()
        return row["n"] if row else 0
