#!/bin/bash
# Re-provision zones 2 and 3 with the irrigation config commands.
# Run on the Ubuntu server. Safe to re-run.
IOTA=http://localhost:4041
H_CT="Content-Type: application/json"
H_SVC="fiware-service: smartfarm"
H_PATH="fiware-servicepath: /"

for N in 2 3; do
  DEV=$(printf "zone%03d" $N)
  ENT=$(printf "urn:ngsi-ld:Zone:%03d" $N)

  echo "=== $DEV ==="
  curl -sX DELETE "$IOTA/iot/devices/$DEV" -H "$H_SVC" -H "$H_PATH" >/dev/null
  sleep 1

  curl -iX POST "$IOTA/iot/devices" -H "$H_CT" -H "$H_SVC" -H "$H_PATH" -d "{\"devices\":[{
    \"device_id\":\"$DEV\",
    \"entity_name\":\"$ENT\",
    \"entity_type\":\"Zone\",
    \"transport\":\"MQTT\",
    \"protocol\":\"PDI-IoTA-UltraLight\",
    \"timezone\":\"Europe/Sofia\",
    \"attributes\":[
      {\"object_id\":\"t\",\"name\":\"airTemperature\",\"type\":\"Number\"},
      {\"object_id\":\"h\",\"name\":\"airHumidity\",\"type\":\"Number\"},
      {\"object_id\":\"sm\",\"name\":\"soilMoisture\",\"type\":\"Number\"},
      {\"object_id\":\"st\",\"name\":\"soilTemperature\",\"type\":\"Number\"},
      {\"object_id\":\"ec\",\"name\":\"soilConductivity\",\"type\":\"Number\"},
      {\"object_id\":\"ph\",\"name\":\"soilPH\",\"type\":\"Number\"},
      {\"object_id\":\"fr\",\"name\":\"flowRate\",\"type\":\"Number\"},
      {\"object_id\":\"vt\",\"name\":\"volumeTotal\",\"type\":\"Number\"},
      {\"object_id\":\"is\",\"name\":\"irrigationState\",\"type\":\"Number\"},
      {\"object_id\":\"mn\",\"name\":\"moistureMin\",\"type\":\"Number\"},
      {\"object_id\":\"mx\",\"name\":\"moistureMax\",\"type\":\"Number\"},
      {\"object_id\":\"vs\",\"name\":\"valveState\",\"type\":\"Number\"},
      {\"object_id\":\"ps\",\"name\":\"pumpState\",\"type\":\"Number\"},
      {\"object_id\":\"ts\",\"name\":\"observedAt\",\"type\":\"DateTime\"}
    ],
    \"commands\":[
      {\"name\":\"valve\",\"type\":\"command\"},
      {\"name\":\"setmin\",\"type\":\"command\"},
      {\"name\":\"setmax\",\"type\":\"command\"},
      {\"name\":\"setauto\",\"type\":\"command\"}
    ]}]}"
  echo
done

echo "=== registered devices ==="
curl -s "$IOTA/iot/devices" -H "$H_SVC" -H "$H_PATH" | grep -o '"device_id":"[^"]*"'
