"""Plant catalogue backed by an external MySQL instance.

Agronomic parameters (moisture band, temperature limits, stage timings) are
curated in the `smartfarm` schema rather than embedded in code, so the
catalogue can be extended without redeploying. Reads are cached because the
catalogue is consulted on every zone render but changes rarely, and a cached
or seeded copy is served if the database host is unreachable.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pymysql
import pymysql.cursors

CACHE_TTL = float(os.getenv("PLANT_CACHE_TTL", "60"))
_cache = {"data": {}, "at": 0.0}
_lock = threading.Lock()

# Offline seed: the catalogue lives on a separate host, so a cold start with
# that host down would otherwise leave every zone unlabelled. The suffix makes
# the degraded state visible rather than silent.
_FALLBACK = {
    "basil":         {"label": "Basil (offline)", "min": 35.0, "max": 50.0, "tmin": 5.0, "tmax": 38.0, "days": 60,
                      "stages": [{"name": "germination", "startDay": 0}, {"name": "vegetative", "startDay": 7},
                                 {"name": "maturation", "startDay": 25}, {"name": "harvest", "startDay": 45}]},
    "cherry_tomato": {"label": "Cherry Tomato (offline)", "min": 35.0, "max": 48.0, "tmin": 10.0, "tmax": 35.0, "days": 75,
                      "stages": [{"name": "germination", "startDay": 0}, {"name": "vegetative", "startDay": 8},
                                 {"name": "maturation", "startDay": 30}, {"name": "harvest", "startDay": 55}]},
    "green_onion":   {"label": "Green Onion (offline)", "min": 30.0, "max": 45.0, "tmin": 2.0, "tmax": 38.0, "days": 55,
                      "stages": [{"name": "germination", "startDay": 0}, {"name": "vegetative", "startDay": 8},
                                 {"name": "maturation", "startDay": 25}, {"name": "harvest", "startDay": 42}]},
    "lettuce":       {"label": "Lettuce (offline)", "min": 40.0, "max": 55.0, "tmin": 2.0, "tmax": 26.0, "days": 45,
                      "stages": [{"name": "germination", "startDay": 0}, {"name": "vegetative", "startDay": 6},
                                 {"name": "maturation", "startDay": 20}, {"name": "harvest", "startDay": 35}]},
    "mint":          {"label": "Mint (offline)", "min": 40.0, "max": 58.0, "tmin": 2.0, "tmax": 36.0, "days": 70,
                      "stages": [{"name": "germination", "startDay": 0}, {"name": "vegetative", "startDay": 10},
                                 {"name": "maturation", "startDay": 30}, {"name": "harvest", "startDay": 50}]},
    "radish":        {"label": "Radish (offline)", "min": 32.0, "max": 44.0, "tmin": 2.0, "tmax": 36.0, "days": 28,
                      "stages": [{"name": "germination", "startDay": 0}, {"name": "vegetative", "startDay": 4},
                                 {"name": "maturation", "startDay": 12}, {"name": "harvest", "startDay": 22}]},
    "spinach":       {"label": "Spinach (offline)", "min": 38.0, "max": 52.0, "tmin": 0.0, "tmax": 24.0, "days": 40,
                      "stages": [{"name": "germination", "startDay": 0}, {"name": "vegetative", "startDay": 6},
                                 {"name": "maturation", "startDay": 18}, {"name": "harvest", "startDay": 32}]},
    "fallow":        {"label": "- Empty -", "min": None, "max": None, "tmin": None, "tmax": None, "days": None,
                      "stages": []},
}


def _connect():
    return pymysql.connect(
        host=os.getenv("CATALOG_DB_HOST", "192.168.0.131"),
        port=int(os.getenv("CATALOG_DB_PORT", "3306")),
        user=os.getenv("CATALOG_DB_USER", "smartfarm"),
        password=os.getenv("CATALOG_DB_PASSWORD", ""),
        database=os.getenv("CATALOG_DB_NAME", "smartfarm"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=5,
    )


def _num(value):
    return None if value is None else float(value)


def _load():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT plant_key, label, moisture_min, moisture_max, "
                "temp_min, temp_max, days_to_harvest FROM plants ORDER BY label"
            )
            plant_rows = cur.fetchall()
            cur.execute(
                "SELECT plant_key, stage_name, start_day FROM plant_stages "
                "ORDER BY plant_key, start_day"
            )
            stage_rows = cur.fetchall()
    finally:
        conn.close()

    catalog = {}
    for row in plant_rows:
        catalog[row["plant_key"]] = {
            "label": row["label"],
            "min": _num(row["moisture_min"]),
            "max": _num(row["moisture_max"]),
            "tmin": _num(row["temp_min"]),
            "tmax": _num(row["temp_max"]),
            "days": row["days_to_harvest"],
            "stages": [],
        }
    for row in stage_rows:
        entry = catalog.get(row["plant_key"])
        if entry is not None:
            entry["stages"].append({"name": row["stage_name"],
                                    "startDay": int(row["start_day"])})
    return catalog


def get_plants(force=False):
    now = time.time()
    with _lock:
        if _cache["data"] and not force and (now - _cache["at"] < CACHE_TTL):
            return _cache["data"]
    try:
        data = _load()
    except Exception as exc:
        print("PLANT CATALOG LOAD FAILED:", repr(exc), flush=True)
        with _lock:
            return _cache["data"] or _FALLBACK
    with _lock:
        _cache["data"] = data
        _cache["at"] = now
    return data


def parse_planted_at(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def growth_stage(planted_at, profile):
    stages = (profile or {}).get("stages") or []
    total = (profile or {}).get("days")
    if not planted_at or not total or not stages:
        return "n/a", 0
    elapsed = (datetime.now(timezone.utc) - planted_at).days
    if elapsed < 0:
        return "not planted", 0
    name = stages[0]["name"]
    for stage in stages:
        if elapsed >= stage["startDay"]:
            name = stage["name"]
    return name, elapsed


def stage_schedule(planted_at, profile):
    """Projected calendar window per stage. Nominal estimates, not observed."""
    stages = (profile or {}).get("stages") or []
    total = (profile or {}).get("days")
    if not planted_at or not total or not stages:
        return [], None

    start = planted_at.date()
    today = datetime.now(timezone.utc).date()
    plan = []

    for index, stage in enumerate(stages):
        start_day = int(stage["startDay"])
        next_day = (int(stages[index + 1]["startDay"])
                    if index + 1 < len(stages) else int(total))
        end_day = max(next_day - 1, start_day)
        start_date = start + timedelta(days=start_day)
        end_date = start + timedelta(days=end_day)

        if today < start_date:
            state = "upcoming"
        elif today > end_date:
            state = "done"
        else:
            state = "current"

        plan.append({"name": stage["name"], "state": state,
                     "startDay": start_day, "endDay": end_day,
                     "startDate": start_date.isoformat(),
                     "endDate": end_date.isoformat()})

    if plan and not any(item["state"] == "current" for item in plan) and today >= start:
        plan[-1]["state"] = "current"

    return plan, (start + timedelta(days=int(total))).isoformat()
