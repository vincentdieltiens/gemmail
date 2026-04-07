# GemMail

Assistant local de gestion d'emails professionnels Office 365, propulsé par Gemma 4 via Ollama.

## Objectif

GemMail analyse automatiquement les emails entrants, propose des classements, rédige des résumés et présente les décisions à valider dans une interface web locale. L'humain garde le contrôle — le LLM propose, l'utilisateur valide.

## Fonctionnement général

Un daemon Python interroge régulièrement la boîte mail via Microsoft Graph API. Chaque nouvel email est analysé par Gemma 4 (modèle local via Ollama). Le résultat de l'analyse (catégorie proposée, importance, action requise, résumé) est stocké en base et présenté dans une interface web pour validation.

Certaines actions peuvent être automatiques (expéditeur connu, email purement informatif), le reste attend une validation explicite de l'utilisateur.

## Architecture

```
Docker Compose
├── ollama           — Serveur LLM local (Gemma 4 e2b)
├── gemmail-daemon   — Daemon Python : polling Graph API + analyse LLM
└── gemmail-web      — Interface web : FastAPI + HTMX (port 8080)

Volumes partagés (montés sur le host)
├── data/gemmail.db  — Base SQLite (emails, décisions, logs)
└── config.yml       — Configuration des dossiers et règles
```

Le daemon et l'interface web communiquent exclusivement via la base SQLite. Pas d'API interne entre les deux services.

## Composants

### Daemon (`gemmail-daemon`)

- Polling Microsoft Graph API toutes les 60-120 secondes
- Récupération des dossiers Outlook existants à chaque cycle (pour contexte LLM)
- Analyse de chaque nouvel email par Gemma 4 :
  - Catégorie proposée (parmi les dossiers existants ou nouveau)
  - Niveau d'importance (haute / moyenne / basse)
  - Action requise (oui / non)
  - Résumé en une phrase
  - Proposition de réponse si nécessaire
- Écriture du résultat en base avec statut `en_attente` ou `automatique`
- Exécution des actions automatiques validées (marquage lu, déplacement)

### Interface web (`gemmail-web`)

FastAPI + HTMX, accessible sur `http://localhost:8080`.

**File d'attente** (page principale)
- Liste des emails en attente de décision
- Pour chaque email : expéditeur, objet, résumé LLM, proposition de classement
- Actions disponibles : valider le classement, choisir un autre dossier, valider la suppression, valider/modifier la réponse proposée, marquer comme lu

**Historique / Logs**
- Tout ce qui s'est passé : actions automatiques et actions validées manuellement
- Filtrable par date, type d'action, expéditeur

**Configuration**
- Liste des dossiers Outlook avec leur description (utilisée comme contexte LLM)
- Ajout/modification de descriptions de dossiers
- Gestion des règles automatiques (ex : expéditeur connu → dossier sans validation)

### Modèle LLM

- Modèle : `gemma4:e2b` (7.2 Go, Q4_K_M)
- Exécution : CPU uniquement (i7-1355U, 16 Go RAM)
- Temps d'analyse : ~1-3 minutes par email (acceptable)
- Contexte fourni au LLM à chaque analyse :
  - Liste des dossiers existants avec descriptions
  - Expéditeur, objet, corps de l'email

### Base de données (SQLite)

Tables principales :
- `emails` — emails analysés avec résultat LLM et statut de traitement
- `actions` — file d'attente des décisions (en attente / validée / rejetée)
- `dossiers` — cache des dossiers Outlook + descriptions locales
- `logs` — historique complet de toutes les opérations
- `regles` — règles automatiques définies par l'utilisateur

### Configuration (`config.yml`)

Fichier YAML éditable manuellement ou via l'interface :

```yaml
polling_interval: 60          # secondes entre chaque vérification
ollama_model: gemma4:e2b

dossiers:
  NomDossier: "Description pour le LLM"
  "Sous-dossier/Enfant": "Description"

regles_automatiques:
  - expediteur: "newsletter@example.com"
    action: marquer_lu
  - expediteur: "factures@fournisseur.com"
    dossier: "Fournisseur/Factures"
    action: déplacer
```

## Comportement de classification

Le LLM reçoit la liste des dossiers existants et leurs descriptions. Il choisit le dossier le plus approprié parmi ceux-ci. Si aucun ne convient, il propose un nouveau nom.

- **Confiance haute + dossier connu** → action automatique (log uniquement)
- **Dossier connu mais hésitation** → proposition dans la file d'attente
- **Nouveau dossier proposé** → validation requise + saisie de description
- **Suppression proposée** → validation requise
- **Réponse nécessaire** → brouillon proposé, validation + envoi manuel

## Stack technique

| Composant | Technologie |
|---|---|
| Langage | Python 3.12 |
| LLM | Gemma 4 e2b via Ollama |
| API emails | Microsoft Graph API (OAuth2, device code flow) |
| Auth | MSAL (token mis en cache dans `data/`) |
| Interface web | FastAPI + HTMX + CSS minimal |
| Base de données | SQLite |
| Conteneurisation | Docker Compose |
| Notifications | Interface web (badge file d'attente) |

## Structure du projet

```
gemmail/
├── docker-compose.yml
├── Dockerfile                  # image gemmail-daemon et gemmail-web
├── requirements.txt
├── config.yml                  # configuration utilisateur
├── .env                        # credentials Azure (non versionné)
├── .env.example
├── README.md
├── data/                       # volume Docker — non versionné
│   ├── gemmail.db
│   ├── token_cache.json
│   └── logs/
└── src/
    ├── main.py                 # point d'entrée daemon
    ├── graph_client.py         # Microsoft Graph API
    ├── llm_client.py           # appels Ollama
    ├── db.py                   # accès SQLite
    ├── config.py               # lecture config.yml
    ├── processor.py            # logique d'analyse et de décision
    └── web/
        ├── app.py              # application FastAPI
        ├── templates/          # templates HTML (Jinja2 + HTMX)
        │   ├── base.html
        │   ├── queue.html
        │   ├── history.html
        │   └── config.html
        └── static/
            └── style.css
```

## Démarrage

### Prérequis

- Docker + Docker Compose
- Un compte Office 365
- Une app Azure AD enregistrée avec la permission `Mail.ReadWrite`

### Installation

```bash
# Cloner le projet
git clone ...
cd gemmail

# Configurer les credentials
cp .env.example .env
# Éditer .env avec AZURE_CLIENT_ID et AZURE_TENANT_ID

# Configurer les dossiers (optionnel au démarrage)
cp config.yml.example config.yml

# Lancer
docker compose up
```

Au premier démarrage :
1. Ollama télécharge le modèle `gemma4:e2b` (~7 Go)
2. Une URL + code s'affiche dans les logs pour l'authentification Microsoft
3. Une fois authentifié, le daemon démarre et l'interface est accessible sur `http://localhost:8080`

## Garanties de traitement

- **Idempotence** : chaque email possède un identifiant unique (fourni par Graph API). Le daemon vérifie systématiquement si cet identifiant est déjà en base avant toute analyse. Un email ne peut jamais être traité deux fois, même si le daemon redémarre.

- **Date de début** : le paramètre `start_date` dans `config.yml` (format `YYYY-MM-DD`) limite le traitement aux emails reçus à partir de cette date. Les emails antérieurs sont ignorés, qu'ils soient lus ou non. Cela permet de démarrer proprement sans retraiter tout l'historique.

```yaml
start_date: "2026-04-07"   # ignorer tout ce qui est antérieur à cette date
```

## Limitations connues

- Traitement CPU uniquement (pas de GPU intégré Intel utilisé) : ~1-3 min/email
- Pas d'accès mobile (usage local uniquement pour l'instant)
- Le token Microsoft expire — MSAL le renouvelle automatiquement tant que le daemon tourne
