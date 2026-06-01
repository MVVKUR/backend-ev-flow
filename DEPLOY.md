# Deploying EV-FLOW (Podman)

Runs the FastAPI backend as a single slim container (~350 MB, no analysis/geo stack) with
**host networking**, fronted by a **Cloudflare Tunnel** for HTTPS.

## What the frontend hits

Once the tunnel is up, the public base URL is your tunnel hostname:

```
https://<your-domain>/api/v1/...      e.g. https://ev-flow-api.opensoft.id/api/v1/stations.geojson
https://<your-domain>/docs            Swagger UI
https://<your-domain>/openapi.json    machine-readable contract
```

Full endpoint contract + examples: [FRONTEND_API.md](FRONTEND_API.md).

## Why host networking

Many cheap VPSes are **LXC/OpenVZ containers**, not full VMs. Their kernel blocks the
iptables NAT that Podman's default bridge network needs, so `compose up` fails with an
`ip_tables: Operation not permitted` error. `network_mode: host` (in `podman-compose.yml`)
skips the bridge entirely, so the API just binds `0.0.0.0:8000` on the host. Works on LXC and
normal VMs alike. (Check your box with `systemd-detect-virt`.)

## Prerequisites (on the VPS)

```bash
sudo dnf install -y podman podman-compose        # Fedora/RHEL
# sudo apt install -y podman podman-compose       # Ubuntu/Debian
```

## Deploy

```bash
git clone <repo> && cd backend-ev-flow

# 1. Provide station data (the API serves empty until this exists)
mkdir -p data/raw data/processed
#   put the 3 source files in data/raw/:
#     _petaspklu_all.json   ocm_jakarta.json   osm_charging_jakarta.json
#   (optional, for /route) build the road graph once on a machine with osmnx:
#     python scripts/build_road_graph.py   -> data/processed/jakarta_drive.graphml

# 2. (optional) configure
cp .env.deploy.example .env        # CORS_ALLOW_ORIGINS, WEB_CONCURRENCY

# 3. Build + run
podman compose up -d --build
podman compose ps

# 4. Check it locally on the VPS
curl -s http://localhost:8000/health        # {"status":"ok","stations_loaded":N,...}
podman logs -f ev-flow-api
```

> Manage it with `podman compose up -d` / `down` / `ps`, or directly:
> `podman logs -f ev-flow-api`, `podman restart ev-flow-api`.

## HTTPS via Cloudflare Tunnel

No open ports, no iptables, ideal for LXC. In the Cloudflare Zero Trust dashboard
(Networks → Tunnels), create a tunnel, install its connector on the VPS, then add a
**Public Hostname**:

| Field | Value |
|---|---|
| Subdomain / Domain | e.g. `ev-flow-api` / `opensoft.id` |
| Path | **leave empty** (so all routes pass through) |
| Service Type | **HTTP** |
| Service URL | **`localhost:8000`** |

The frontend then uses `https://ev-flow-api.opensoft.id`.

## Updating

```bash
git pull
podman compose up -d --build       # rebuilds + restarts

# refresh station data without rebuilding: replace files in data/raw, then
podman compose restart api         # reloads the in-memory dataset
```

## Keep it running after a reboot

```bash
loginctl enable-linger $USER       # lets the restart=unless-stopped container come back
```

## Pre-public checklist

- [x] Slim image, runs as non-root.
- [x] HTTPS via Cloudflare Tunnel.
- [x] CORS configurable (`CORS_ALLOW_ORIGINS`); `*` is OK for read-only public data.
- [x] ReDoS fixed: `q`/`city` searches are literal (no regex injection / 500s).
- [ ] Station data present in `data/raw/` (else `/health` shows `stations_loaded: 0`).
- [ ] Rate limiting: the API has none; Cloudflare (in front of the tunnel) can add it.
- [ ] Routing graph in `data/processed/` if you want `/route` (otherwise it returns 503).
