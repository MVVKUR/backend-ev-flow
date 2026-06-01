# Deploying EV-FLOW (Podman, public)

Runs the FastAPI backend behind a Caddy reverse proxy (automatic HTTPS). The image is the
**slim API only** (~150 MB) — none of the heavy analysis/geo stack.

## What the frontend hits

After deploy, the public base URL is:

```
https://<DOMAIN>/api/v1/...        ← e.g. https://api.evflow.id/api/v1/stations.geojson
https://<DOMAIN>/docs              ← Swagger UI
https://<DOMAIN>/openapi.json      ← machine-readable contract
```

If you deploy without a domain (IP only), it's `http://<server-ip>/...` (plain HTTP on :80).
Full endpoint contract + examples: **[FRONTEND_API.md](FRONTEND_API.md)**.

## Prerequisites (on the VPS)

```bash
sudo apt update && sudo apt install -y podman podman-compose   # Ubuntu/Debian
# (Fedora/RHEL: dnf install podman podman-compose)
```

## Deploy

```bash
git clone <repo> && cd backend-ev-flow

# 1. Provide the station data (the API serves empty until this exists)
mkdir -p data/raw data/processed
#   put the 3 source files in data/raw/:
#     _petaspklu_all.json   ocm_jakarta.json   osm_charging_jakarta.json
#   (optional, for /route) build the road graph once on a machine with osmnx:
#     python scripts/build_road_graph.py   -> data/processed/jakarta_drive.graphml

# 2. Configure
cp .env.deploy.example .env
nano .env            # set DOMAIN (your domain) and CORS_ALLOW_ORIGINS

# 3. Run
podman-compose up -d --build

# 4. Check
curl -s https://<DOMAIN>/health        # {"status":"ok","stations_loaded":N,...}
podman-compose logs -f api
```

Point your domain's **A record** at the server and open ports **80 + 443** in the firewall —
Caddy then issues a Let's Encrypt cert automatically. No domain yet? Leave `DOMAIN=` empty and
it serves HTTP on :80; the frontend uses `http://<ip>/...` (note: browsers block HTTP calls
from an HTTPS page, so get a domain before going live).

## Updating

```bash
git pull
podman-compose up -d --build        # rebuilds the API image, restarts

# refresh station data without rebuilding: replace files in data/raw, then
podman-compose restart api          # reloads the in-memory dataset
```

## Architecture

```
Internet ──443/80──> caddy (TLS, gzip) ──:8000──> api (uvicorn, WEB_CONCURRENCY workers)
                                                     └─ reads ./data (mounted read-only)
```
- `api` is **not** published directly — only Caddy is exposed. (Uncomment `ports:` in
  `podman-compose.yml` to expose 8000 directly for testing.)
- `./data` is mounted read-only; the API only reads it.

## Pre-public checklist

- [x] **Slim image** — only API deps, runs as non-root.
- [x] **HTTPS** via Caddy (set `DOMAIN`).
- [x] **CORS** configurable (`CORS_ALLOW_ORIGINS`); `*` is OK for read-only public data, lock it once auth lands.
- [x] **ReDoS fixed** — `q`/`city` searches are literal (no regex injection / 500s).
- [ ] **Station data present** in `data/raw/` (else `/health` shows `stations_loaded: 0`).
- [ ] **Rate limiting** — the API has none. For a public endpoint, front it with Cloudflare
      (free) or add a Caddy `rate_limit` plugin. Recommended before heavy traffic.
- [ ] **Routing graph** in `data/processed/` if you want `/route` (otherwise it returns 503).
