"""
Reservoir interlock  --  cross-zone dry-run protection.

Only zone 1 carries the water-level probe, but all three zone nodes drive the
same pump through a wired-OR line to relay IN4. Without a shared view of the
reservoir, zone 2 or 3 can start a dose against an empty tank and run the pump
dry until their own 15 s flow check trips.

This service subscribes to zone 1's waterLevel via Orion and pushes a
`valve close` command to the other zones the moment it drops below the cutout,
turning a per-node protection into a system-wide interlock.

Flow:
    Zone1 ESP32 -> MQTT -> IoT Agent -> Orion --notify--> THIS
                                          ^                 |
                                          +--- valve close --+
"""

import logging
import os
import threading
import time

import requests
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Configuration (override with environment variables in docker-compose)
# ---------------------------------------------------------------------------
ORION = os.getenv("ORION_URL", "http://orion:1026")
FIWARE_SERVICE = os.getenv("FIWARE_SERVICE", "smartfarm")
FIWARE_SERVICEPATH = os.getenv("FIWARE_SERVICEPATH", "/")

SOURCE_ENTITY = os.getenv("SOURCE_ENTITY", "urn:ngsi-ld:Zone:001")
PROTECTED_ENTITIES = os.getenv(
    "PROTECTED_ENTITIES", "urn:ngsi-ld:Zone:002,urn:ngsi-ld:Zone:003"
).split(",")

# Must match LEVEL_MIN_ABORT / LEVEL_MIN_START in the zone 1 firmware.
LEVEL_ABORT = float(os.getenv("LEVEL_ABORT", "12"))
LEVEL_CLEAR = float(os.getenv("LEVEL_CLEAR", "25"))

# While locked out, re-send the close command periodically in case a node
# rebooted, missed the first one, or reconnected after the fact.
REPEAT_INTERVAL_S = int(os.getenv("REPEAT_INTERVAL_S", "300"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
)
log = logging.getLogger("interlock")

app = Flask(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_lock = threading.Lock()
state = {
    "locked": False,
    "last_level": None,
    "last_seen": None,
    "last_command_at": 0.0,
    "commands_sent": 0,
    "errors": 0,
}


def _headers():
    return {
        "Content-Type": "application/json",
        "fiware-service": FIWARE_SERVICE,
        "fiware-servicepath": FIWARE_SERVICEPATH,
    }


def send_valve_close(entity_id):
    """PATCH a valve command onto an entity. The IoT Agent forwards it to the
    device over MQTT and the ESP32 acknowledges on its cmdexe topic."""
    url = f"{ORION}/v2/entities/{entity_id}/attrs"
    body = {"valve": {"type": "command", "value": "close"}}
    try:
        r = requests.patch(url, json=body, headers=_headers(), timeout=5)
        if r.status_code in (204, 200):
            log.info("valve close -> %s", entity_id)
            state["commands_sent"] += 1
            return True
        log.error("valve close -> %s failed: HTTP %s %s",
                  entity_id, r.status_code, r.text[:200])
    except requests.RequestException as exc:
        log.error("valve close -> %s failed: %s", entity_id, exc)
    state["errors"] += 1
    return False


def close_protected_zones(reason):
    log.warning("INTERLOCK ENGAGED (%s) - closing %d zone(s)",
                reason, len(PROTECTED_ENTITIES))
    for entity_id in PROTECTED_ENTITIES:
        send_valve_close(entity_id.strip())
    state["last_command_at"] = time.time()


def evaluate(level):
    """Act only on transitions, with hysteresis, so a value hovering near the
    threshold does not produce a command storm."""
    with _lock:
        state["last_level"] = level
        state["last_seen"] = time.time()

        if not state["locked"] and level < LEVEL_ABORT:
            state["locked"] = True
            close_protected_zones(f"level {level:.1f}% < {LEVEL_ABORT}%")

        elif state["locked"] and level >= LEVEL_CLEAR:
            state["locked"] = False
            log.info("INTERLOCK CLEARED - level %.1f%% >= %.1f%%; zones may "
                     "resume under their own logic", level, LEVEL_CLEAR)

        elif state["locked"]:
            # Still low. Re-assert periodically rather than every notification.
            if time.time() - state["last_command_at"] >= REPEAT_INTERVAL_S:
                close_protected_zones(f"still low at {level:.1f}%")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/interlock", methods=["POST"])
def interlock():
    """NGSIv2 notification endpoint. Payload shape:
       {"subscriptionId": "...", "data": [{"id": "...", "waterLevel": {...}}]}"""
    payload = request.get_json(silent=True) or {}
    entities = payload.get("data", [])

    if not entities:
        log.warning("notification with no data block")
        return jsonify({"status": "ignored", "reason": "no data"}), 200

    for entity in entities:
        if entity.get("id") != SOURCE_ENTITY:
            continue
        attr = entity.get("waterLevel")
        if not isinstance(attr, dict) or attr.get("value") is None:
            continue
        try:
            evaluate(float(attr["value"]))
        except (TypeError, ValueError):
            log.warning("unparseable waterLevel: %r", attr.get("value"))

    return jsonify({"status": "ok", "locked": state["locked"]}), 200


@app.route("/status", methods=["GET"])
def status():
    age = None
    if state["last_seen"]:
        age = round(time.time() - state["last_seen"], 1)
    return jsonify({
        "locked": state["locked"],
        "last_level": state["last_level"],
        "seconds_since_update": age,
        "commands_sent": state["commands_sent"],
        "errors": state["errors"],
        "thresholds": {"abort": LEVEL_ABORT, "clear": LEVEL_CLEAR},
        "protected": PROTECTED_ENTITIES,
    }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "up"}), 200


if __name__ == "__main__":
    log.info("reservoir interlock starting")
    log.info("  source     : %s", SOURCE_ENTITY)
    log.info("  protecting : %s", ", ".join(PROTECTED_ENTITIES))
    log.info("  abort <%.1f%%   clear >=%.1f%%", LEVEL_ABORT, LEVEL_CLEAR)
    app.run(host="0.0.0.0", port=5001)
