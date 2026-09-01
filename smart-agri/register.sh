#!/usr/bin/env bash
# register.sh — Provision IoT service + 3 Zone devices + QuantumLeap subscription
# Run from ~/smart-agri after docker-compose up -d

ORION="http://localhost:1026"
IOT="http://localhost:4041"
QL="http://localhost:8668"
SVC="smart"
SP="/agri"

GRN='\033[0;32m'; YLW='\033[1;33m'; RST='\033[0m'
ok()   { echo -e "${GRN}✔  $*${RST}"; }
info() { echo -e "\n${YLW}▸  $*${RST}"; }

# ── Wait helpers ──────────────────────────────────────────────────────────────
wait_for() {
  local label=$1 url=$2
  printf "${YLW}▸  Waiting for %s ${RST}" "${label}"
  while ! curl -sf "${url}" > /dev/null 2>&1; do printf "."; sleep 3; done
  echo ""
  ok "${label} ready"
}

wait_for "Orion"      "${ORION}/version"
wait_for "IoT Agent"  "${IOT}/iot/about"
wait_for "QuantumLeap" "${QL}/version"

# ── [1/6] Service group ───────────────────────────────────────────────────────
info "[1/6] Registering IoT service group..."
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${IOT}/iot/services" \
  -H "Content-Type: application/json" \
  -H "fiware-service: ${SVC}" \
  -H "fiware-servicepath: ${SP}" \
  -d '{
    "services": [{
      "apikey":      "agrikey",
      "cbroker":     "http://orion:1026",
      "entity_type": "Zone",
      "resource":    "/iot/d"
    }]
  }')
ok "Service group → HTTP ${HTTP}  (201=created, 409=already exists — both OK)"

# ── [2–4/6] Devices ───────────────────────────────────────────────────────────
for i in 1 2 3; do
  PAD=$(printf "%03d" "${i}")
  info "[$(( i + 1 ))/6] Registering zone${i}  →  urn:ngsi-ld:Zone:${PAD}"

  TMP=$(mktemp /tmp/zone_XXXXXX.json)
  cat > "${TMP}" << ENDJSON
{
  "devices": [{
    "device_id":   "zone${i}",
    "entity_name": "urn:ngsi-ld:Zone:${PAD}",
    "entity_type": "Zone",
    "protocol":    "PDI-IoTA-UltraLight",
    "transport":   "MQTT",
    "attributes": [
      {"object_id": "m",  "name": "moisture",    "type": "Number"},
      {"object_id": "t",  "name": "temperature", "type": "Number"},
      {"object_id": "ec", "name": "ec",          "type": "Number"},
      {"object_id": "ph", "name": "ph",          "type": "Number"},
      {"object_id": "n",  "name": "N",           "type": "Number"},
      {"object_id": "p",  "name": "P",           "type": "Number"},
      {"object_id": "k",  "name": "K",           "type": "Number"},
      {"object_id": "fl", "name": "flow",        "type": "Number"},
      {"object_id": "r",  "name": "rain",        "type": "Number"}
    ]
  }]
}
ENDJSON

  HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${IOT}/iot/devices" \
    -H "Content-Type: application/json" \
    -H "fiware-service: ${SVC}" \
    -H "fiware-servicepath: ${SP}" \
    -d "@${TMP}")
  rm -f "${TMP}"
  ok "zone${i} → HTTP ${HTTP}"
done

# ── [5/6] Orion → QuantumLeap subscription ────────────────────────────────────
info "[5/6] Creating Orion → QuantumLeap subscription..."
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${ORION}/v2/subscriptions" \
  -H "Content-Type: application/json" \
  -H "fiware-service: ${SVC}" \
  -H "fiware-servicepath: ${SP}" \
  -d '{
    "description": "Persist all Zone attribute changes to QuantumLeap / CrateDB",
    "subject": {
      "entities": [{"idPattern": ".*", "type": "Zone"}],
      "condition": {
        "attrs": ["moisture","temperature","ec","ph","N","P","K","flow","rain"]
      }
    },
    "notification": {
      "http": {"url": "http://quantumleap:8668/v2/notify"},
      "attrs": ["moisture","temperature","ec","ph","N","P","K","flow","rain"],
      "metadata": ["dateCreated","dateModified"]
    },
    "throttling": 1
  }')
ok "Subscription → HTTP ${HTTP}  (201=created)"

# ── [6/6] Verify ──────────────────────────────────────────────────────────────
info "[6/6] Verifying Orion entities..."
curl -s "${ORION}/v2/entities" \
  -H "fiware-service: ${SVC}" \
  -H "fiware-servicepath: ${SP}" | python3 -m json.tool

ok "All done — 3 Zone entities should be listed above."

cat << 'TIPS'

══════════════════════════════════════════════════════════════════
 NEXT: send test data so CrateDB gets its first rows
══════════════════════════════════════════════════════════════════
mosquitto_pub -h localhost -t /agrikey/zone1/attrs \
  -m "m|45.2|t|22.1|ec|1.2|ph|6.8|n|30|p|20|k|25|fl|0.5|r|0"
mosquitto_pub -h localhost -t /agrikey/zone2/attrs \
  -m "m|38.0|t|21.5|ec|1.0|ph|7.0|n|25|p|18|k|22|fl|0.3|r|0"
mosquitto_pub -h localhost -t /agrikey/zone3/attrs \
  -m "m|52.1|t|23.0|ec|1.5|ph|6.5|n|35|p|22|k|28|fl|0.7|r|1"

# Then query QuantumLeap for stored history:
curl -s "http://localhost:8668/v2/entities/urn:ngsi-ld:Zone:001/attrs/moisture" \
  -H "fiware-service: smart" \
  -H "fiware-servicepath: /agri" | python3 -m json.tool

# CrateDB Admin UI (open in browser):
#   http://192.168.0.131:4200
══════════════════════════════════════════════════════════════════
TIPS
