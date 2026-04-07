import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import graph_client
import db
import config

app = FastAPI(title="GemMail")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _get_token():
    return graph_client.get_access_token()


# --- Pages principales ---

@app.get("/", response_class=HTMLResponse)
async def queue(request: Request):
    en_traitement = db.get_emails_by_statut("en_traitement")
    en_attente = db.get_emails_by_statut("en_attente")
    counts = db.count_emails_by_statut()
    dossiers = db.get_all_dossiers()
    return templates.TemplateResponse("queue.html", {
        "request": request,
        "en_traitement": en_traitement,
        "en_attente": en_attente,
        "counts": counts,
        "dossiers": dossiers,
    })


@app.get("/history", response_class=HTMLResponse)
async def history(request: Request, offset: int = 0):
    emails = db.get_all_emails(limit=50, offset=offset)
    logs = db.get_logs(limit=100)
    counts = db.count_emails_by_statut()
    return templates.TemplateResponse("history.html", {
        "request": request,
        "emails": emails,
        "logs": logs,
        "counts": counts,
        "offset": offset,
    })


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    dossiers = db.get_all_dossiers()
    cfg = config.load()
    counts = db.count_emails_by_statut()
    return templates.TemplateResponse("config.html", {
        "request": request,
        "dossiers": dossiers,
        "cfg": cfg,
        "counts": counts,
    })


# --- Actions sur les emails ---

@app.post("/email/{email_id}/validate")
async def validate_email(
    email_id: str,
    dossier: str = Form(...),
    action: str = Form("deplacer"),
    description_nouveau: str = Form(""),
):
    """Valide une action proposée par le LLM."""
    token = _get_token()

    # Si c'est un nouveau dossier, le créer d'abord
    email = db.get_email(email_id)
    if email and email["llm_nouveau_dossier"]:
        folder = graph_client.create_folder(token, dossier)
        db.upsert_dossier({
            "id": folder["id"],
            "nom": folder["displayName"],
            "chemin": folder["displayName"],
            "parent_id": None,
            "last_sync": "",
        })
        if description_nouveau:
            config.save_dossier_description(folder["displayName"], description_nouveau)

    folder_row = db.get_dossier_by_chemin(dossier)
    if folder_row:
        graph_client.move_to_folder(token, email_id, folder_row["id"])
        graph_client.mark_as_read(token, email_id)

    db.update_email_decided(email_id, dossier, action)
    db.add_log(email_id, "action_manuelle", f"Validé manuellement : {action} → {dossier}")
    return RedirectResponse("/", status_code=303)


@app.post("/email/{email_id}/mark-read")
async def mark_read(email_id: str):
    token = _get_token()
    graph_client.mark_as_read(token, email_id)
    db.update_email_decided(email_id, None, "marquer_lu")
    db.add_log(email_id, "action_manuelle", "Marqué comme lu")
    return RedirectResponse("/", status_code=303)


@app.post("/email/{email_id}/delete")
async def delete_email(email_id: str):
    token = _get_token()
    graph_client.delete_email(token, email_id)
    db.update_email_decided(email_id, None, "supprimer")
    db.add_log(email_id, "action_manuelle", "Supprimé")
    return RedirectResponse("/", status_code=303)


@app.post("/email/{email_id}/ignore")
async def ignore_email(email_id: str):
    db.update_email_decided(email_id, None, "ignorer")
    db.add_log(email_id, "action_manuelle", "Ignoré")
    return RedirectResponse("/", status_code=303)


# --- Configuration ---

@app.post("/config/dossier/{dossier_id}/description")
async def update_description(dossier_id: str, description: str = Form(...)):
    dossiers = db.get_all_dossiers()
    for d in dossiers:
        if d["id"] == dossier_id:
            config.save_dossier_description(d["chemin"], description)
            db.update_dossier_description(dossier_id, description)
            break
    return RedirectResponse("/config", status_code=303)


# --- Fragments HTMX ---

@app.get("/htmx/counts", response_class=HTMLResponse)
async def htmx_counts(request: Request):
    counts = db.count_emails_by_statut()
    n = counts.get("en_attente", 0) + counts.get("en_traitement", 0)
    return HTMLResponse(str(n) if n else "")


@app.get("/htmx/queue-items", response_class=HTMLResponse)
async def htmx_queue(request: Request):
    en_traitement = db.get_emails_by_statut("en_traitement")
    en_attente = db.get_emails_by_statut("en_attente")
    dossiers = db.get_all_dossiers()
    return templates.TemplateResponse("_queue_items.html", {
        "request": request,
        "en_traitement": en_traitement,
        "en_attente": en_attente,
        "dossiers": dossiers,
    })
