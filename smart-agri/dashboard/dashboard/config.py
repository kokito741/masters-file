import os


class Config:
    ORION_URL = os.getenv("ORION_URL", "http://orion:1026")
    QUANTUMLEAP_URL = os.getenv("QUANTUMLEAP_URL", "http://quantumleap:8668")
    MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
    MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
    FIWARE_SERVICE = os.getenv("FIWARE_SERVICE", "smartfarm")
    FIWARE_SERVICEPATH = os.getenv("FIWARE_SERVICEPATH", "/")
    FIWARE_API_KEY = os.getenv("FIWARE_API_KEY", "")
    KEYROCK_URL = os.getenv("KEYROCK_URL", "http://keyrock:3005")
    # Browser-facing: the authorize redirect is followed by the user's browser,
    # which cannot resolve the container name.
    KEYROCK_PUBLIC_URL = os.getenv("KEYROCK_PUBLIC_URL", os.getenv("KEYROCK_URL", "http://keyrock:3005"))
    KEYROCK_CLIENT_ID = os.getenv("KEYROCK_CLIENT_ID", "")
    KEYROCK_CLIENT_SECRET = os.getenv("KEYROCK_CLIENT_SECRET", "")
    CALLBACK_URL = os.getenv("CALLBACK_URL", "http://localhost:5000/callback")
    GRAFANA_PANEL_URL = os.getenv("GRAFANA_PANEL_URL", "")
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5"))
    MQTT_ACK_TIMEOUT = float(os.getenv("MQTT_ACK_TIMEOUT", "5"))


ZONE_MAP = {
    "urn:ngsi-ld:Zone:001": "zone001",
    "urn:ngsi-ld:Zone:002": "zone002",
    "urn:ngsi-ld:Zone:003": "zone003",
}
DEVICE_TO_ENTITY = {v: k for k, v in ZONE_MAP.items()}
ALLOWED_ZONE_INPUTS = set(ZONE_MAP.keys()) | set(ZONE_MAP.values())

IRRIGATION_STATE_LABELS = {
    0: "IDLE",
    1: "WATERING",
    2: "SOAKING",
    3: "LOCKOUT",
    4: "MANUAL",
}

IRRIGATION_STATE_CLASSES = {
    0: "badge-idle",
    1: "badge-watering",
    2: "badge-soaking",
    3: "badge-lockout",
    4: "badge-manual",
}
