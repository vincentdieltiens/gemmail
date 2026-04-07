import os
from pathlib import Path

import msal
import requests

TOKEN_CACHE_PATH = Path("/app/data/token_cache.json")
SCOPES = ["Mail.ReadWrite"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _build_app():
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        cache.deserialize(TOKEN_CACHE_PATH.read_text())

    app = msal.PublicClientApplication(
        client_id=os.environ["AZURE_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{os.environ['AZURE_TENANT_ID']}",
        token_cache=cache,
    )
    return app, cache


def _save_cache(cache: msal.SerializableTokenCache):
    if cache.has_state_changed:
        TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE_PATH.write_text(cache.serialize())


def get_access_token() -> str:
    app, cache = _build_app()
    accounts = app.get_accounts()
    result = None

    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        print("\n" + "=" * 60)
        print("AUTHENTIFICATION MICROSOFT REQUISE")
        print(flow["message"])
        print("=" * 60 + "\n")
        result = app.acquire_token_by_device_flow(flow)

    _save_cache(cache)

    if "access_token" not in result:
        raise RuntimeError(f"Échec auth: {result.get('error_description')}")

    return result["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def get_new_emails(token: str, since: str, top: int = 20) -> list[dict]:
    """
    Récupère les emails non lus reçus après `since` (format ISO 8601 : YYYY-MM-DD).
    Retourne au plus `top` emails par appel pour ne pas saturer le LLM.
    """
    url = (
        f"{GRAPH_BASE}/me/mailFolders/inbox/messages"
        f"?$filter=isRead eq false and receivedDateTime ge {since}T00:00:00Z"
        f"&$top={top}"
        f"&$select=id,subject,from,receivedDateTime,bodyPreview,body,importance"
        f"&$orderby=receivedDateTime asc"
    )
    resp = requests.get(url, headers=_headers(token), timeout=30)
    resp.raise_for_status()
    return resp.json().get("value", [])


def get_folders(token: str) -> list[dict]:
    """
    Récupère récursivement tous les dossiers mail de l'utilisateur.
    Retourne une liste de dicts {id, nom, chemin, parent_id}.
    """
    folders = []
    _fetch_folders_recursive(token, "msgfolders", folders, parent_path="", parent_id=None)
    return folders


def _fetch_folders_recursive(token: str, endpoint: str, result: list, parent_path: str, parent_id: str | None):
    url = f"{GRAPH_BASE}/me/mailFolders?$top=100"
    if endpoint != "msgfolders":
        url = f"{GRAPH_BASE}/me/mailFolders/{endpoint}/childFolders?$top=100"

    resp = requests.get(url, headers=_headers(token), timeout=15)
    if resp.status_code != 200:
        return

    for folder in resp.json().get("value", []):
        chemin = f"{parent_path}/{folder['displayName']}" if parent_path else folder["displayName"]
        result.append({
            "id": folder["id"],
            "nom": folder["displayName"],
            "chemin": chemin,
            "parent_id": parent_id,
        })
        if folder.get("childFolderCount", 0) > 0:
            _fetch_folders_recursive(token, folder["id"], result, chemin, folder["id"])


def mark_as_read(token: str, email_id: str):
    requests.patch(
        f"{GRAPH_BASE}/me/messages/{email_id}",
        headers=_headers(token),
        json={"isRead": True},
        timeout=10,
    ).raise_for_status()


def move_to_folder(token: str, email_id: str, folder_id: str):
    requests.post(
        f"{GRAPH_BASE}/me/messages/{email_id}/move",
        headers=_headers(token),
        json={"destinationId": folder_id},
        timeout=10,
    ).raise_for_status()


def create_folder(token: str, name: str, parent_id: str | None = None) -> dict:
    """Crée un dossier (à la racine ou sous un parent). Retourne le dossier créé."""
    if parent_id:
        url = f"{GRAPH_BASE}/me/mailFolders/{parent_id}/childFolders"
    else:
        url = f"{GRAPH_BASE}/me/mailFolders"

    resp = requests.post(
        url,
        headers=_headers(token),
        json={"displayName": name},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def delete_email(token: str, email_id: str):
    requests.delete(
        f"{GRAPH_BASE}/me/messages/{email_id}",
        headers=_headers(token),
        timeout=10,
    ).raise_for_status()


def send_reply(token: str, email_id: str, body: str):
    requests.post(
        f"{GRAPH_BASE}/me/messages/{email_id}/reply",
        headers=_headers(token),
        json={"message": {}, "comment": body},
        timeout=15,
    ).raise_for_status()
