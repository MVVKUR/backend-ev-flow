#!/usr/bin/env bash
# Smoke-test every EV-FLOW endpoint against a base URL.
#
#   scripts/smoke_test.sh                              # defaults to localhost:8000
#   scripts/smoke_test.sh https://ev-flow-api.opensoft.id
#
# PASS = the endpoint responded with an expected status. Station endpoints return 200
# with empty results until data/raw/ is populated; routing returns 503 until the road
# graph is built (both are treated as PASS here).
set -u
BASE="${1:-http://localhost:8000}"
pass=0; fail=0

check() {  # label  path  "expected codes"
  local label="$1" path="$2" expected="$3" code
  code=$(curl -s -o /tmp/ev_body -w '%{http_code}' "$BASE$path")
  if [[ " $expected " == *" $code "* ]]; then
    printf '  \033[32mPASS\033[0m %-3s %s\n' "$code" "$label"; pass=$((pass+1))
  else
    printf '  \033[31mFAIL\033[0m %-3s %s (expected: %s)\n' "$code" "$label" "$expected"; fail=$((fail+1))
  fi
}

first_id() {  # path  -> prints items[0].id (needs python3; empty if none)
  curl -s "$BASE$1" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["items"][0]["id"] if d.get("items") else "")' 2>/dev/null
}

echo "Testing $BASE"

echo "System & lookups"
check "GET /health"                  "/health"                "200"
check "GET /api/v1/stats"            "/api/v1/stats"          "200"
check "GET /api/v1/sources"          "/api/v1/sources"        "200"
check "GET /api/v1/provinces"        "/api/v1/provinces"      "200"
check "GET /api/v1/cities"           "/api/v1/cities"         "200"
check "GET /api/v1/connectors"       "/api/v1/connectors"     "200"
check "GET /api/v1/speed-tiers"      "/api/v1/speed-tiers"    "200"

echo "Stations (discovery)"
check "GET /api/v1/stations"         "/api/v1/stations?limit=2" "200"
check "GET /api/v1/stations (filtered)" "/api/v1/stations?connector_type=CCS2&speed_tier=fast&limit=2" "200"
check "GET /api/v1/stations.geojson" "/api/v1/stations.geojson?bbox=106.55,-6.65,107.10,-5.95&limit=5" "200"
check "GET /api/v1/stations/nearby"  "/api/v1/stations/nearby?lat=-6.2088&lon=106.8456&radius_km=5&limit=3" "200"
SID=$(first_id "/api/v1/stations?limit=1")
if [[ -n "$SID" ]]; then check "GET /api/v1/stations/{id}" "/api/v1/stations/$SID" "200"
else echo "  SKIP -   GET /api/v1/stations/{id}  (no station data — populate data/raw/)"; fi

echo "EV model catalogue"
check "GET /api/v1/ev-models"        "/api/v1/ev-models?limit=2" "200"
MID=$(first_id "/api/v1/ev-models?limit=1")
if [[ -n "$MID" ]]; then check "GET /api/v1/ev-models/{id}" "/api/v1/ev-models/$MID" "200"
else echo "  SKIP -   GET /api/v1/ev-models/{id}  (catalogue empty?)"; fi

echo "Routing  (503 = graph not built, 404 = no station data — both OK)"
check "GET /api/v1/route"            "/api/v1/route?lat=-6.2088&lon=106.8456&dest_lat=-6.18&dest_lon=106.83" "200 404 503"
check "GET /api/v1/route/nearest-station" "/api/v1/route/nearest-station?lat=-6.2088&lon=106.8456" "200 404 503"

echo
echo "Passed: $pass   Failed: $fail"
[[ $fail -eq 0 ]]
