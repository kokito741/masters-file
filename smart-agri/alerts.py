import os, time, requests
from flask import Flask, request

APP = Flask(__name__)
PO_TOKEN = os.environ["PUSHOVER_TOKEN"]
PO_USER  = os.environ["PUSHOVER_USER"]

# Thresholds are resolved from Orion, not held locally: the dashboard writes
# them when a plant is selected, so this service never disagrees with the
# firmware. Orion sends only CHANGED attributes, so notifications rarely carry
# them - hence the cache, refreshed on miss and at startup.
ORION   = os.getenv("ORION_URL", "http://localhost:1026")
HEADERS = {"fiware-service": "smartfarm", "fiware-servicepath": "/"}
KNOWN   = ("urn:ngsi-ld:Zone:001", "urn:ngsi-ld:Zone:002", "urn:ngsi-ld:Zone:003")
_cfg = {}

def refresh(eid):
    try:
        r = requests.get(f"{ORION}/v2/entities/{eid}?options=keyValues",
                         headers=HEADERS, timeout=5)
        r.raise_for_status()
        d = r.json()
    except Exception as exc:
        print("CFG FETCH FAILED", eid, repr(exc), flush=True)
        return _cfg.get(eid)
    cfg = {"name": d.get("plantLabel") or eid.split(":")[-1],
           "min":  d.get("moistureMin"),
           "tmin": d.get("tempMin"),
           "tmax": d.get("tempMax")}
    _cfg[eid] = cfg
    print("CFG", eid, cfg, flush=True)
    return cfg

def config_for(e, eid):
    # Use values carried in the notification when present, else cached.
    fresh = {"name": e.get("plantLabel", {}).get("value") if isinstance(e.get("plantLabel"), dict) else None,
             "min":  val(e, "moistureMin"),
             "tmin": val(e, "tempMin"),
             "tmax": val(e, "tempMax")}
    cfg = _cfg.get(eid)
    # plantType only changes on re-planting, and Orion notifies changed
    # attributes only - so its presence is the signal to re-read thresholds.
    if cfg is None or "plantType" in e:
        cfg = refresh(eid) or cfg or {}
    merged = dict(cfg)
    for k, v in fresh.items():
        if v is not None:
            merged[k] = v
    if merged != cfg and merged:
        _cfg[eid] = merged
    return merged

COOLDOWN      = 1800    # dry soil, reservoir
TEMP_COOLDOWN = 21600   # air temperature
HYST      = 3
TEMP_HYST = 2
_state = {}

def push(title, msg, prio=0):
    try:
        r = requests.post("https://api.pushover.net/1/messages.json", data={
            "token":PO_TOKEN,"user":PO_USER,"title":title,"message":msg,
            "priority":prio,"sound":"siren" if prio>0 else "pushover"}, timeout=10)
        print("PUSH", r.status_code, r.text.strip(), "|", title, "|", msg, flush=True)
        return r.status_code == 200
    except Exception as e:
        print("PUSH EXCEPTION:", repr(e), flush=True)
        return False

def evaluate(eid, kind, trip, clear, title, msg, prio=0, cooldown=COOLDOWN):
    st = _state.setdefault((eid,kind), {"active":False,"last":0})
    now = time.time()
    print(f"  eval {kind} active={st['active']} trip={trip} clear={clear}", flush=True)
    if st["active"]:
        if clear:
            push(title, "Recovered - " + msg, -1)
            st["active"] = False; st["last"] = 0
        elif now - st["last"] > cooldown:
            if push(title, msg, prio): st["last"] = now
    elif trip:
        if push(title, msg, prio):
            st["last"] = now; st["active"] = True

def val(e, k):
    a = e.get(k)
    if not isinstance(a, dict): return None
    try: return float(a["value"])
    except (TypeError, ValueError, KeyError): return None

@APP.route("/notify", methods=["POST"])
def notify():
    body = request.get_json(force=True, silent=True)
    if not body:
        print("BAD BODY:", request.data[:200], flush=True)
        return "", 204
    for e in body.get("data", []):
        eid = e.get("id")
        if eid not in KNOWN:
            print("ENTITY", eid, "UNKNOWN", flush=True)
            continue
        z = config_for(e, eid)
        print("ENTITY", eid, "known" if z else "UNKNOWN", list(e.keys()), flush=True)
        sm = val(e,"soilMoisture")
        at = val(e,"airTemperature")
        wl = val(e,"waterLevel")
        print(f"  sm={sm} air={at} wl={wl}", flush=True)

        if sm is not None and z.get("min") is not None:
            evaluate(eid, "dry", sm < z["min"], sm >= z["min"] + HYST,
                     f"{z['name']} dry", f"Soil {sm:.1f}% (min {z['min']}%)", 1)

        if at is not None and z.get("tmin") is not None and z.get("tmax") is not None:
            evaluate(eid, "airtemp",
                     not (z["tmin"] <= at <= z["tmax"]),
                     z["tmin"]+TEMP_HYST <= at <= z["tmax"]-TEMP_HYST,
                     f"{z['name']} air temp",
                     f"Air {at:.1f}C outside {z['tmin']}-{z['tmax']}C",
                     0, TEMP_COOLDOWN)

        if wl is not None:
            evaluate(eid, "tank", wl < 15, wl > 25,
                     "Reservoir low", f"Level {wl:.0f}%", 1)
    return "", 204

for _eid in KNOWN:
    refresh(_eid)

APP.run(host="0.0.0.0", port=5002)
