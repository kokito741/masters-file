#!/bin/bash
# Recreate the QuantumLeap subscription including moistureMin / moistureMax.
set -e
ORION=http://localhost:1026
H_SVC="fiware-service: smartfarm"
H_PATH="fiware-servicepath: /"

echo "== removing existing QuantumLeap subscription(s) =="
for id in $(curl -s "$ORION/v2/subscriptions" -H "$H_SVC" -H "$H_PATH" \
            | jq -r '.[] | select(.notification.http.url | contains("quantumleap")) | .id'); do
  echo "   deleting $id"
  curl -sX DELETE "$ORION/v2/subscriptions/$id" -H "$H_SVC" -H "$H_PATH"
done

ATTRS='["soilMoisture","soilTemperature","airTemperature","airHumidity","soilConductivity","soilPH","flowRate","volumeTotal","waterLevel","irrigationState","moistureMin","moistureMax","valveState","pumpState"]'

echo "== creating replacement =="
curl -iX POST "$ORION/v2/subscriptions" \
  -H "Content-Type: application/json" -H "$H_SVC" -H "$H_PATH" \
  -d "{
    \"description\": \"Zone history to QuantumLeap\",
    \"subject\": {
      \"entities\": [{\"idPattern\": \"urn:ngsi-ld:Zone:.*\", \"type\": \"Zone\"}],
      \"condition\": {\"attrs\": $ATTRS}
    },
    \"notification\": {
      \"http\": {\"url\": \"http://quantumleap:8668/v2/notify\"},
      \"attrs\": $ATTRS,
      \"metadata\": [\"dateCreated\", \"dateModified\"]
    },
    \"throttling\": 1
  }"

echo
echo "== active subscriptions =="
curl -s "$ORION/v2/subscriptions" -H "$H_SVC" -H "$H_PATH" \
  | jq -r '.[] | "\(.id)  ->  \(.notification.http.url)  [\(.status)]"'
