# Enterprise RAG Chatbot — v2.0.8

Production-grade, role-based Retrieval-Augmented Generation (RAG) system.

**v2.0.8 changes from v1.x (Streamlit):**
- Frontend replaced with **Open WebUI** — polished, mobile-friendly chat UI
- Backend replaced with **FastAPI + Uvicorn** — full REST API, OpenAPI docs
- All services run in **Docker Desktop** — one command to start
- **Nginx** reverse proxy with JWT `auth_request` validation on every API call
- **Auto-login** — visiting `http://your-server/?token=<jwt>` logs the user straight into Open WebUI
- **SQL Server and PostgreSQL/pgvector are external** — connect to your own existing instances

---

## Architecture

```
Browser
  │
  │  http://your-server/?token=<enterprise_jwt>
  ▼
┌──────────────────────────────────────────────────────────────┐
│  Nginx :80  (Docker)                                         │
│                                                              │
│  /?token=<jwt>    ──► /api/v1/auth/owui-login                │
│  /auth-proxy/*    ──► owui-auth-proxy :8081  (no auth guard) │
│  /api/*           ──► backend :8000          (JWT guard)     │
│  /                ──► open-webui :8080       (cookie)        │
└──────┬───────────────────┬──────────────────┬────────────────┘
       │  (Docker)         │  (Docker)        │  (Docker)
┌──────▼──────┐   ┌────────▼────────┐  ┌─────▼──────────┐
│  FastAPI    │   │  Auth Proxy     │  │  Open WebUI    │
│  Backend    │   │  :8081          │  │  :8080         │
│  :8000      │   └─────────────────┘  └────────────────┘
└──────┬──────┘
       │
       ├──────────────────────────────────────────────────────┐
       │  (Docker)                                            │
┌──────▼──────────┐                                           │
│  Model Server   │   ┌─────────────────┐  ┌──────────────┐  │
│  (Embeddings)   │   │  SQL Server     │  │  PostgreSQL  │  │
│  :8502          │   │  ** EXTERNAL ** │  │  + pgvector  │  │
└─────────────────┘   │  your server   │  │  ** EXTERNAL*│  │
                      └────────────────┘  └──────────────┘  │
                               ▲                  ▲          │
                               └──────────────────┴──────────┘
                                  backend connects over network
```

### Dockerised services (5 containers)
| Container | Role | Port |
|-----------|------|------|
| `nginx` | Reverse proxy + JWT `auth_request` | 80 / 443 |
| `backend` | FastAPI + Uvicorn REST API | internal :8000 |
| `model-server` | Sentence-Transformer embeddings | internal :8502 |
| `open-webui` | Chat frontend | internal :8080 |
| `owui-auth-proxy` | JWT → Open WebUI auto-login bridge | internal :8081 |

### External services (you supply these)
| Service | Used for |
|---------|----------|
| **SQL Server** | Users, roles, sources, chat history |
| **PostgreSQL + pgvector** | Document chunk embeddings (vector store) |

---

## Auto-Login Flow (`?token=`)

```
1. User visits:   http://your-server/?token=<enterprise_jwt>
2. Nginx:         Rewrites to /api/v1/auth/owui-login?token=<jwt>
3. Backend:       Validates JWT → issues one-time token (60 s TTL)
                  Redirects browser to /auth-proxy/login?token=<one_time>
4. Auth Proxy:    Exchanges one-time token with backend (server-to-server)
                  Provisions user in Open WebUI (creates account if new)
                  Signs in as that user → gets Open WebUI session token
                  Sets browser cookie → redirects to /
5. Open WebUI:    User is logged in, model pre-configured, ready to chat
```

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Docker Desktop 4.25+ | Enable WSL2 backend on Windows |
| Your SQL Server | TCP/IP enabled, EnterpriseRAG database created |
| Your PostgreSQL + pgvector | pgvector extension installed, rag_vectors database created |
| HuggingFace model | `all-MiniLM-L6-v2` downloaded locally |
| Custom LLM API | Your existing hosted model endpoint |

---

## Quick Start

### Step 1 — Configure `.env`

```cmd
copy .env.example .env
```

Open `.env` and set **all** of the following:

```dotenv
# Your external SQL Server
DB_HOST=192.168.1.100        # hostname or IP of your SQL Server
DB_PORT=1433
DB_NAME=EnterpriseRAG
DB_USERNAME=sa
DB_PASSWORD=YourStrongPassword!

# Your external PostgreSQL + pgvector
PGVECTOR_HOST=192.168.1.101  # hostname or IP of your PostgreSQL server
PGVECTOR_PORT=5432
PGVECTOR_DB=rag_vectors
PGVECTOR_USER=postgres
PGVECTOR_PASSWORD=YourPGPassword!

# Your LLM API
LLM_TOKEN_URL=https://your-llm/api/v1/token
LLM_GENERATE_URL=https://your-llm/api/v1/generate-text
LLM_USERNAME=your_user
LLM_PASSWORD=your_password

# Open WebUI admin account (created on first run)
OWUI_ADMIN_EMAIL=admin@yourcompany.com
OWUI_ADMIN_PASSWORD=StrongAdminPass!
OWUI_SECRET_KEY=<run: openssl rand -hex 32>
OWUI_AUTO_LOGIN_SECRET=<run: openssl rand -hex 32>

# Local testing only — set false for production
APP_DEV_MODE=true
```

### Step 2 — Apply database schemas (first time only)

**Windows:**
```cmd
scripts\setup-external-db.bat
```

**Linux / macOS:**
```bash
chmod +x scripts/setup-external-db.sh
./scripts/setup-external-db.sh
```

Or apply manually in SSMS / pgAdmin:
- SQL Server → run `sql\01_schema.sql` against database `EnterpriseRAG`
- PostgreSQL → run `sql\02_pgvector_schema.sql` against database `rag_vectors`

Make sure pgvector is installed first:
```sql
-- Run in PostgreSQL as superuser
CREATE EXTENSION IF NOT EXISTS vector;
```

### Step 3 — Load the embedding model

**Windows:**
```cmd
scripts\load-model.bat "E:\Libraries\Sentence Transformer\all-MiniLM-L6-v2"
```

**Linux / macOS:**
```bash
chmod +x scripts/load-model.sh
./scripts/load-model.sh /opt/models/all-MiniLM-L6-v2
```

### Step 4 — Start

**Windows (double-click):** `START.bat`

**Windows (PowerShell):**
```powershell
.\scripts\Start-RAG.ps1
```

**Linux / macOS:**
```bash
chmod +x scripts/*.sh start.sh stop.sh
./start.sh
```

### Step 5 — Verify

```cmd
scripts\healthcheck.bat
```
```bash
./scripts/healthcheck.sh
```

Expected:
```
✅  Nginx liveness       (200)
✅  Nginx readiness      (200)
✅  Backend full health  (200)
✅  Auth proxy health    (200)
✅  Open WebUI root      (200)
✅  API docs             (200)
✅  Model list (no JWT)  (401)
```

### Step 6 — Access

| URL | Description |
|-----|-------------|
| `http://localhost/?token=<jwt>` | **Auto-login** with Enterprise RAG JWT |
| `http://localhost/` | Open WebUI direct (needs OWUI account) |
| `http://localhost/api/docs` | Interactive REST API docs |
| `http://localhost/api/v1/health` | System health JSON |

---

## External Database Requirements

### SQL Server

- TCP/IP protocol **must be enabled** (SQL Server Configuration Manager → Protocols for MSSQLSERVER → TCP/IP → Enabled)
- Firewall must allow the Docker host machine to reach port 1433
- The `EnterpriseRAG` database must exist before first startup
- User must have `db_owner` or equivalent rights on `EnterpriseRAG`
- ODBC Driver 18 is bundled inside the backend Docker image — no host install needed

### PostgreSQL + pgvector

- The `pgvector` extension must be installed:
  ```sql
  CREATE EXTENSION IF NOT EXISTS vector;
  ```
- `pg_hba.conf` must allow connections from the Docker host machine (or subnet)
- The `rag_vectors` database must exist:
  ```sql
  CREATE DATABASE rag_vectors;
  ```
- Schema is auto-applied by the backend on first startup — or run `sql/02_pgvector_schema.sql` manually

---

## REST API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/health` | None | System health + DB checks |
| GET | `/api/v1/health/live` | None | Liveness probe |
| GET | `/api/v1/health/ready` | None | Readiness probe |
| GET | `/api/v1/auth/owui-login` | None | JWT → OWUI auto-login |
| POST | `/api/v1/auth/verify` | None | JWT validation (Nginx sub-request) |
| GET | `/api/v1/auth/me` | JWT | Current user profile |
| GET | `/api/v1/sources/` | JWT | List all sources |
| GET | `/api/v1/sources/types` | JWT | Available source types |
| GET | `/api/v1/sources/{id}` | JWT | Get one source |
| POST | `/api/v1/sources/` | Admin | Create source |
| PUT | `/api/v1/sources/{id}` | Admin | Update source |
| DELETE | `/api/v1/sources/{id}` | Admin | Delete source |
| POST | `/api/v1/sources/{id}/reindex` | Admin | Re-index source |
| POST | `/api/v1/sources/upload` | Admin | Upload source file |
| GET | `/api/v1/indexing/status` | Admin | Indexing status |
| POST | `/api/v1/indexing/refresh-all` | Admin | Re-index all |
| POST | `/api/v1/indexing/refresh/{id}` | Admin | Re-index one |
| POST | `/api/v1/chat/query` | JWT | RAG query |
| POST | `/api/v1/chat/completions` | JWT | OpenAI-compatible (Open WebUI) |
| GET | `/api/v1/chat/models` | JWT | Model list (OpenAI format) |
| POST | `/api/v1/chat/sessions` | JWT | New chat session |
| GET | `/api/v1/chat/sessions/{id}/messages` | JWT | Session history |
| POST | `/api/v1/chat/upload` | JWT | Upload file for chat |
| GET | `/api/v1/admin/health` | Admin | Admin health dashboard |
| GET | `/api/v1/admin/vector-stats` | Admin | Vector chunk stats |
| GET | `/api/v1/admin/users` | Admin | All users |
| GET | `/api/v1/admin/roles` | Admin | All roles |
| GET | `/api/v1/owui/status` | Admin | Open WebUI connectivity |
| POST | `/api/v1/owui/sync` | Admin | Force model re-registration |

Interactive docs: `http://localhost/api/docs`

---

## Scripts Reference

### Windows — double-click launchers (project root)

| File | Action |
|------|--------|
| `START.bat` | Full setup wizard + start all Docker services |
| `STOP.bat` | Stop all Docker services |
| `HEALTHCHECK.bat` | Check all service endpoints |

### Windows — CMD (`scripts\`)

```cmd
scripts\start.bat                             REM full setup + start
scripts\start.bat /skipbuild                  REM restart without rebuild
scripts\stop.bat                              REM stop, keep data
scripts\stop.bat /reset                       REM stop + wipe all Docker volumes
scripts\setup-external-db.bat                 REM apply schemas to your DBs (first time)
scripts\load-model.bat "C:\path\to\model"     REM load embedding model
scripts\logs.bat                              REM tail all logs
scripts\logs.bat backend                      REM tail one service
scripts\healthcheck.bat                       REM check all endpoints
```

### Windows — PowerShell (`scripts\`)

```powershell
.\scripts\Start-RAG.ps1                       # full setup + start
.\scripts\Start-RAG.ps1 -SkipBuild            # restart without rebuild
.\scripts\Start-RAG.ps1 -SkipBuild -SkipModelCheck  # fastest restart
.\scripts\Start-RAG.ps1 -Logs                 # start + tail logs
.\scripts\Start-RAG.ps1 -Down                 # stop all services
.\scripts\Stop-RAG.ps1                        # stop, keep data
.\scripts\Stop-RAG.ps1 -Reset                 # stop + wipe all Docker volumes
.\scripts\Load-Model.ps1 -ModelPath "C:\path" # load model
```

> **PowerShell policy:** If blocked, run `START.bat` (bypasses automatically) or:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### Linux / macOS (`scripts/`)

```bash
chmod +x scripts/*.sh start.sh stop.sh        # one-time permission fix

./start.sh                      # full setup + start
./start.sh --skip-build         # restart without rebuild
./start.sh --skip-model         # skip model check (fastest restart)
./start.sh --logs               # start + tail logs
./start.sh --down               # stop all services

./stop.sh                       # stop, keep data
./stop.sh --reset               # stop + wipe all Docker volumes

./scripts/setup-external-db.sh  # apply schemas (first time only)
./scripts/load-model.sh /path   # load embedding model
./scripts/healthcheck.sh        # check all endpoints

docker compose logs -f           # tail all logs
docker compose logs -f backend   # tail backend only
```

### Typical first-run sequence (Windows)

```
1.  Edit .env  (fill in DB_HOST, PGVECTOR_HOST, passwords, LLM URLs)
2.  Double-click  scripts\setup-external-db.bat   (apply schemas once)
3.  Double-click  START.bat                        (builds images + starts)
4.  Enter model path when prompted
5.  Wait ~2 minutes for all services to become healthy
6.  Visit  http://localhost/?token=<your_jwt>
```

### Typical restart after PC reboot

```cmd
START.bat   (or)   .\scripts\Start-RAG.ps1 -SkipBuild -SkipModelCheck
```

---

## Docker Management

```bash
# View container status
docker compose ps

# Restart one service after config change
docker compose restart backend

# Rebuild one image after code change
docker compose build backend
docker compose up -d --no-deps backend

# Open a shell in the backend
docker exec -it rag-backend bash

# Hard reset — wipe all Docker volumes (DESTRUCTIVE, keeps your external DBs)
docker compose down -v
```

---

## Production Checklist

- [ ] Set `APP_DEV_MODE=false` in `.env`
- [ ] Generate RSA key pair and configure your IdP (see `backend/conf/jwt_public_key.pem`)
- [ ] Set all strong passwords in `.env`
- [ ] Generate random `OWUI_SECRET_KEY` and `OWUI_AUTO_LOGIN_SECRET`
- [ ] Configure `jwt.audience` and `jwt.issuer` in `backend/conf/app.properties`
- [ ] Verify SQL Server TCP/IP is enabled and port 1433 is reachable from Docker host
- [ ] Verify pgvector extension installed and `pg_hba.conf` allows Docker host
- [ ] Enable HTTPS — add TLS cert to `nginx/certs/` and update `nginx/conf.d/default.conf`
- [ ] Set `indexing.api_key` in `app.properties`
- [ ] Set `secure=True` on the auth proxy cookie (after enabling HTTPS)

---

## File Structure

```
enterprise_rag_v2.0.8/
│
├── START.bat / STOP.bat / HEALTHCHECK.bat  ← Windows double-click launchers
├── start.sh / stop.sh                      ← Linux/macOS root launchers
├── docker-compose.yml                      ← 5 Docker services (no DB containers)
├── .env.example                            ← Environment variable template
├── .gitignore
│
├── nginx/
│   ├── nginx.conf
│   └── conf.d/default.conf                 ← Routing + JWT auth_request
│
├── backend/
│   ├── Dockerfile
│   ├── Dockerfile.model_server
│   ├── main.py                             ← FastAPI entry point
│   ├── model_server.py                     ← Embedding model server
│   ├── requirements.txt
│   ├── model_server_requirements.txt
│   ├── conf/
│   │   ├── app.properties                  ← All app config
│   │   └── jwt_public_key.pem
│   └── app/
│       ├── api/routes/                     ← auth, health, sources, indexing,
│       │                                      chat, admin, openwebui_integration
│       ├── core/                           ← dependencies, jwt_auth, embedding, rag
│       ├── db/                             ← db_manager, vector_store, repositories
│       ├── indexing/
│       ├── llm/
│       ├── sources/
│       └── utils/
│
├── openwebui-auth-proxy/
│   ├── Dockerfile
│   └── main.py                             ← JWT → Open WebUI auto-login
│
├── sql/
│   ├── 01_schema.sql                       ← SQL Server schema (run on YOUR server)
│   └── 02_pgvector_schema.sql              ← PostgreSQL schema (run on YOUR server)
│
└── scripts/
    ├── Start-RAG.ps1 / start.bat / start.sh
    ├── Stop-RAG.ps1  / stop.bat  / stop.sh
    ├── Load-Model.ps1 / load-model.bat / load-model.sh
    ├── setup-external-db.bat / setup-external-db.sh  ← apply schemas (first time)
    ├── healthcheck.bat / healthcheck.sh
    └── logs.bat
```

---

## Authentication Guide

### Dev Mode — Bypass Everything (Local Testing)

Set `APP_DEV_MODE=true` in your `.env` file:

```dotenv
APP_DEV_MODE=true
```

**What this does:**
- JWT verification is completely skipped
- A synthetic admin user is automatically injected on every request
- No token is needed anywhere
- All API endpoints and the chat work immediately without any login

**Access in dev mode:**
```
http://localhost/          → Open WebUI (direct, no login prompt)
http://localhost/api/docs  → Full REST API, all endpoints unlocked
```

> ⚠️ **Never set `APP_DEV_MODE=true` in production.** It gives anyone on the network full admin access.

---

### Production Mode — JWT Token Flow

Set `APP_DEV_MODE=false` in `.env`. All access requires a valid RS256 JWT token.

#### How to generate a test JWT token

**Option A — Using Python (quickest for testing):**

```python
# pip install PyJWT cryptography
import jwt, datetime

# Load your private key (generated during setup)
with open("backend/conf/jwt_private_key.pem") as f:
    private_key = f.read()

payload = {
    "sub":   "dev-user-001",      # must match SAU_LOGIN in SPE_ADMIN_USER table
    "name":  "Dev User",
    "email": "dev@example.com",
    "iss":   "enterprise-idp",    # must match jwt.issuer in app.properties
    "aud":   "enterprise-rag",    # must match jwt.audience in app.properties
    "iat":   datetime.datetime.utcnow(),
    "exp":   datetime.datetime.utcnow() + datetime.timedelta(hours=8),
}

token = jwt.encode(payload, private_key, algorithm="RS256")
print(token)
```

**Option B — Using the /api/docs Swagger UI:**
1. Open `http://localhost/api/docs`
2. Click **Authorize** (top right)
3. Paste your JWT token
4. All endpoints are now accessible

**Option C — Generate key pair and token in one step (PowerShell):**
```powershell
# Requires OpenSSL on PATH
openssl genrsa -out backend\conf\jwt_private_key.pem 2048
openssl rsa -in backend\conf\jwt_private_key.pem -pubout -out backend\conf\jwt_public_key.pem

# Then generate token with Python (Option A above)
```

#### Auto-login URL

Once you have a token, use it to log directly into Open WebUI:

```
http://localhost/?token=<your_jwt_token>
```

**Full flow:**
1. Browser visits `http://localhost/?token=eyJhbGci...`
2. Nginx passes it to the backend for validation
3. Backend validates JWT, creates a 60-second one-time token
4. Auth proxy provisions your user in Open WebUI (creates account if new)
5. Browser lands on Open WebUI — fully logged in, no password prompt

#### Calling the REST API directly

```bash
# Store your token
TOKEN="eyJhbGci..."

# Check health (no auth needed)
curl http://localhost/api/v1/health

# Authenticated request
curl http://localhost/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"

# RAG query
curl -X POST http://localhost/api/v1/chat/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the company vacation policy?"}'

# Windows PowerShell
$TOKEN = "eyJhbGci..."
Invoke-RestMethod http://localhost/api/v1/auth/me \
  -Headers @{Authorization = "Bearer $TOKEN"}
```

#### JWT configuration in `backend/conf/app.properties`

These must match your Identity Provider (or your test token generator):

```properties
jwt.algorithm=RS256
jwt.audience=enterprise-rag    # aud claim in your token
jwt.issuer=enterprise-idp      # iss claim in your token
jwt.leeway_seconds=30          # clock skew tolerance
jwt.public_key_path=/app/conf/jwt_public_key.pem
```

#### User must exist in the database

The `sub` claim in the JWT must match `SAU_LOGIN` in the `SPE_ADMIN_USER` table:

```sql
-- Add a user (run against your SQL Server EnterpriseRAG database)
INSERT INTO SPE_ADMIN_USER (SAU_LOGIN, SAU_NAME, SAU_EMAIL)
VALUES ('jdoe@company.com', 'Jane Doe', 'jdoe@company.com');

-- Assign a role (1=admin, 2=analyst, 3=hr, 4=general)
INSERT INTO CIS_MAP_USER_ROLE (CMUR_USER_ID, CMUR_ROLE_ID)
VALUES (SCOPE_IDENTITY(), 4);
```

Default seed users (from `sql/01_schema.sql`):

| SAU_LOGIN | Role | Use for |
|-----------|------|---------|
| `dev-user-001` | general | Testing as a regular user |
| `admin-001` | admin | Testing admin features |

---

### Troubleshooting Auth

| Symptom | Cause | Fix |
|---------|-------|-----|
| `401 Unauthorized` on all requests | Token invalid or missing | Check `Authorization: Bearer <token>` header |
| `Token audience mismatch` | `aud` claim wrong | Set `jwt.audience` to match your token's `aud` |
| `Token issuer mismatch` | `iss` claim wrong | Set `jwt.issuer` to match your token's `iss` |
| `User account not found` | `sub` not in database | Add user to `SPE_ADMIN_USER` table |
| `Login link has expired` | One-time token > 60s old | Regenerate token and try again |
| Auth proxy `Connection refused` | Open WebUI still starting | Wait 2-3 min and retry |
| Auth proxy unhealthy | Open WebUI not ready | Auth proxy now retries for 120s automatically |
