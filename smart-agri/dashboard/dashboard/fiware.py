from datetime import datetime, timedelta, timezone

import requests

from config import DEVICE_TO_ENTITY, IRRIGATION_STATE_CLASSES, IRRIGATION_STATE_LABELS, ZONE_MAP
from plants import get_plants, growth_stage, parse_planted_at, stage_schedule


class FiwareClient:
    def __init__(self, config):
        self.orion_url = config.ORION_URL.rstrip("/")
        self.quantumleap_url = config.QUANTUMLEAP_URL.rstrip("/")
        self.timeout = config.REQUEST_TIMEOUT
        self.headers = {
            "fiware-service": config.FIWARE_SERVICE,
            "fiware-servicepath": config.FIWARE_SERVICEPATH,
            "Accept": "application/json",
        }

    def _parse_observed_at(self, value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _extract_value(self, attr):
        if isinstance(attr, dict) and "value" in attr:
            return attr["value"]
        return attr

    def _to_float(self, value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_zones(self):
        now = datetime.now(timezone.utc)
        url = f"{self.orion_url}/v2/entities"
        params = {"type": "Zone"}
        response = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
        response.raise_for_status()
        entities = response.json()

        zones = []
        for entity in entities:
            entity_id = entity.get("id")
            device_id = ZONE_MAP.get(entity_id)
            if not device_id:
                continue

            observed_at = self._parse_observed_at(self._extract_value(entity.get("observedAt")))
            stale = True
            stale_text = "no timestamp"
            if observed_at:
                stale = now - observed_at > timedelta(minutes=5)
                stale_text = f"no data since {observed_at.astimezone().strftime('%H:%M')}" if stale else ""

            irrigation_value = self._to_float(self._extract_value(entity.get("irrigationState")))
            irrigation_state = int(irrigation_value) if irrigation_value is not None else -1

            catalog = get_plants()
            plant_key = self._extract_value(entity.get("plantType")) or "fallow"
            profile = catalog.get(plant_key) or {"label": "- Unknown -", "days": None, "stages": []}
            planted_at = parse_planted_at(self._extract_value(entity.get("plantedAt")))
            stage, days_elapsed = growth_stage(planted_at, profile)
            stage_plan, harvest_date = stage_schedule(planted_at, profile)

            zone_payload = {
                "entityId": entity_id,
                "zone": device_id,
                "airTemperature": self._to_float(self._extract_value(entity.get("airTemperature"))),
                "airHumidity": self._to_float(self._extract_value(entity.get("airHumidity"))),
                "soilMoisture": self._to_float(self._extract_value(entity.get("soilMoisture"))),
                "soilTemperature": self._to_float(self._extract_value(entity.get("soilTemperature"))),
                "soilConductivity": self._to_float(self._extract_value(entity.get("soilConductivity"))),
                "soilPH": self._to_float(self._extract_value(entity.get("soilPH"))),
                "flowRate": self._to_float(self._extract_value(entity.get("flowRate"))),
                "volumeTotal": self._to_float(self._extract_value(entity.get("volumeTotal"))),
                "waterLevel": self._to_float(self._extract_value(entity.get("waterLevel"))),
                "valveState": self._to_float(self._extract_value(entity.get("valveState"))),
                "pumpState": self._to_float(self._extract_value(entity.get("pumpState"))),
                "irrigationState": irrigation_state,
                "irrigationLabel": IRRIGATION_STATE_LABELS.get(irrigation_state, "UNKNOWN"),
                "irrigationClass": IRRIGATION_STATE_CLASSES.get(irrigation_state, "badge-idle"),
                "moistureMin": self._to_float(self._extract_value(entity.get("moistureMin"))),
                "moistureMax": self._to_float(self._extract_value(entity.get("moistureMax"))),
                "plantType": plant_key,
                "plantLabel": profile.get("label"),
                "plantedAt": planted_at.date().isoformat() if planted_at else None,
                "growthStage": stage,
                "daysElapsed": days_elapsed,
                "stagePlan": stage_plan,
                "harvestDate": harvest_date,
                "daysToHarvest": (
                    (profile["days"] - days_elapsed) if profile.get("days") else None
                ),
                "observedAt": observed_at.isoformat() if observed_at else None,
                "stale": stale,
                "staleText": stale_text,
            }
            zones.append(zone_payload)

        zones.sort(key=lambda item: item["zone"])
        return zones

    def get_zone_by_device(self, device_id):
        entity_id = DEVICE_TO_ENTITY.get(device_id)
        if not entity_id:
            return None

        zones = self.get_zones()
        for zone in zones:
            if zone.get("entityId") == entity_id:
                return zone
        return None

    def get_quantumleap_series(self, entity_id, attrs, limit=50):
        url = f"{self.quantumleap_url}/v2/entities/{entity_id}/attrs"
        params = {"attrs": attrs, "limit": limit}
        response = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def set_plant_metadata(self, device_id, plant_key, planted_at_date):
        """Write plant identity to Orion. Thresholds are NOT written here:
        they are device-owned and arrive via the IoT Agent after the
        firmware persists them to NVS."""
        entity_id = DEVICE_TO_ENTITY.get(device_id)
        if not entity_id:
            raise ValueError("Unknown device id")
        profile = get_plants().get(plant_key)
        if not profile:
            raise ValueError("Unknown plant")

        url = f"{self.orion_url}/v2/entities/{entity_id}/attrs?options=keyValues"
        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"
        payload = {
            "plantType": plant_key,
            "plantLabel": profile["label"],
            "plantedAt": f"{planted_at_date}T00:00:00Z",
        }
        # Advisory air-temperature limits. Published to Orion so the alert
        # service can resolve them from context instead of a local catalog.
        if profile.get("tmin") is not None:
            payload["tempMin"] = profile["tmin"]
            payload["tempMax"] = profile["tmax"]
        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return True
