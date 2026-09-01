#!/usr/bin/env bash
# ============================================================================
# ch3_00_prepare.sh — подготовка на средата за експериментите от глава 3
#
# Прави четири неща:
#   1. пресъздава абонамента за QuantumLeap БЕЗ throttling
#   2. създава втори абонамент за синтетичния тип SynthZone
#   3. открива имената на схемата и таблиците в CrateDB
#   4. проверява достъпността на всички услуги
#
# Пуска се на сървъра. Безопасно е да се пуска повторно.
# ============================================================================
set -euo pipefail

ORION=${ORION:-http://localhost:1026}
CRATE=${CRATE:-http://localhost:4200}
IOTA=${IOTA:-http://localhost:4041}
SVC="fiware-service: smartfarm"
SP="fiware-servicepath: /"

need() { command -v "$1" >/dev/null || { echo "липсва: $1"; exit 1; }; }
need curl; need jq

echo "== 0. достъпност на услугите =="
for pair in "Orion:$ORION/version" "CrateDB:$CRATE/" "IoT Agent:$IOTA/iot/about"; do
  name=${pair%%:*}; url=${pair#*:}
  if curl -sf -o /dev/null "$url"; then echo "   $name OK"; else echo "   $name НЕДОСТЪПЕН ($url)"; exit 1; fi
done

ATTRS='["soilMoisture","soilTemperature","airTemperature","airHumidity","soilConductivity","soilPH","flowRate","volumeTotal","waterLevel","irrigationState","moistureMin","moistureMax","valveState","pumpState"]'

echo
echo "== 1. премахване на throttling от абонамента за Zone =="
for id in $(curl -s "$ORION/v2/subscriptions" -H "$SVC" -H "$SP" \
            | jq -r '.[] | select(.notification.http.url | contains("quantumleap"))
                        | select(.subject.entities[0].type=="Zone") | .id'); do
  echo "   изтриване на $id"
  curl -sX DELETE "$ORION/v2/subscriptions/$id" -H "$SVC" -H "$SP"
done

curl -s -o /dev/null -w "   Zone -> HTTP %{http_code}\n" \
  -X POST "$ORION/v2/subscriptions" -H "Content-Type: application/json" -H "$SVC" -H "$SP" \
  -d "{\"description\":\"Zone history to QuantumLeap (no throttling)\",
       \"subject\":{\"entities\":[{\"idPattern\":\"urn:ngsi-ld:Zone:.*\",\"type\":\"Zone\"}],
                    \"condition\":{\"attrs\":$ATTRS}},
       \"notification\":{\"http\":{\"url\":\"http://quantumleap:8668/v2/notify\"},
                         \"attrs\":$ATTRS,\"metadata\":[\"dateCreated\",\"dateModified\"]}}"

echo
echo "== 2. абонамент за синтетичния тип SynthZone =="
SYNTH_ATTRS='["soilMoisture","soilTemperature","airTemperature","airHumidity","seq"]'
for id in $(curl -s "$ORION/v2/subscriptions" -H "$SVC" -H "$SP" \
            | jq -r '.[] | select(.subject.entities[0].type=="SynthZone") | .id'); do
  curl -sX DELETE "$ORION/v2/subscriptions/$id" -H "$SVC" -H "$SP"
done

curl -s -o /dev/null -w "   SynthZone -> HTTP %{http_code}\n" \
  -X POST "$ORION/v2/subscriptions" -H "Content-Type: application/json" -H "$SVC" -H "$SP" \
  -d "{\"description\":\"SynthZone load test to QuantumLeap\",
       \"subject\":{\"entities\":[{\"idPattern\":\"SynthZone:.*\",\"type\":\"SynthZone\"}],
                    \"condition\":{\"attrs\":$SYNTH_ATTRS}},
       \"notification\":{\"http\":{\"url\":\"http://quantumleap:8668/v2/notify\"},
                         \"attrs\":$SYNTH_ATTRS}}"

echo
echo "== 3. активни абонаменти =="
curl -s "$ORION/v2/subscriptions" -H "$SVC" -H "$SP" \
  | jq -r '.[] | "   \(.subject.entities[0].type)  throttling=\(.throttling // "няма")  \(.status)"'

echo
echo "== 4. схеми и таблици в CrateDB =="
curl -s -X POST "$CRATE/_sql" -H 'Content-Type: application/json' \
  -d '{"stmt":"SELECT table_schema, table_name FROM information_schema.tables WHERE table_name LIKE '"'"'et%'"'"' ORDER BY 1,2"}' \
  | jq -r '.rows[] | "   \(.[0]).\(.[1])"'

echo
echo "== 5. брой редове и обхват по зони =="
curl -s -X POST "$CRATE/_sql" -H 'Content-Type: application/json' \
  -d '{"stmt":"SELECT entity_id, count(*), min(time_index), max(time_index) FROM \"mtsmartfarm\".\"etzone\" GROUP BY 1 ORDER BY 1"}' \
  | jq -r '.rows[]? | "   \(.[0])  редове=\(.[1])  от \(.[2]) до \(.[3])"' \
  || echo "   (проверете името на схемата от стъпка 4)"

echo
echo "Готово. Ако схемата НЕ е mtsmartfarm.etzone, подайте --schema/--table на следващите скриптове."
