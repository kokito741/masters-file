# Reservoir interlock

Cross-zone dry-run protection. Zone 1 owns the water-level probe; all three
nodes share the pump. This service turns zone 1's `waterLevel` into a
system-wide cutout.

## 1. Files

Put `app.py`, `requirements.txt` and `Dockerfile` in `~/smart-agri/interlock/`.

## 2. Add to docker-compose.yml

```yaml
  interlock:
    build: ./interlock
    container_name: interlock
    restart: unless-stopped
    depends_on:
      - orion
    environment:
      - ORION_URL=http://orion:1026
      - FIWARE_SERVICE=smartfarm
      - FIWARE_SERVICEPATH=/
      - SOURCE_ENTITY=urn:ngsi-ld:Zone:001
      - PROTECTED_ENTITIES=urn:ngsi-ld:Zone:002,urn:ngsi-ld:Zone:003
      - LEVEL_ABORT=12
      - LEVEL_CLEAR=25
      - REPEAT_INTERVAL_S=300
    ports:
      - "5001:5001"
    networks:
      - default
```

`LEVEL_ABORT` and `LEVEL_CLEAR` must match `LEVEL_MIN_ABORT` and
`LEVEL_MIN_START` in the zone 1 firmware, or the two will disagree about
when the tank is safe.

## 3. Build and start

```bash
cd ~/smart-agri
docker compose up -d --build interlock
docker logs -f interlock
```

Expect:

```
reservoir interlock starting
  source     : urn:ngsi-ld:Zone:001
  protecting : urn:ngsi-ld:Zone:002, urn:ngsi-ld:Zone:003
  abort <12.0%   clear >=25.0%
```

## 4. Subscribe Orion to it

```bash
curl -iX POST http://localhost:1026/v2/subscriptions \
  -H "Content-Type: application/json" \
  -H "fiware-service: smartfarm" -H "fiware-servicepath: /" \
  -d '{
    "description": "Reservoir interlock: zone 1 water level",
    "subject": {
      "entities": [{"id": "urn:ngsi-ld:Zone:001", "type": "Zone"}],
      "condition": {"attrs": ["waterLevel"]}
    },
    "notification": {
      "http": {"url": "http://interlock:5001/interlock"},
      "attrs": ["waterLevel"]
    },
    "throttling": 5
  }'
```

## 5. Verify

Subscription is delivering (look for `lastSuccess`, not `lastFailure`):

```bash
curl -s http://localhost:1026/v2/subscriptions \
  -H "fiware-service: smartfarm" -H "fiware-servicepath: /"
```

Interlock is seeing values:

```bash
curl -s http://localhost:5001/status
```

```json
{"locked": false, "last_level": 78.4, "seconds_since_update": 31.2, ...}
```

`seconds_since_update` climbing past ~150 means notifications stopped —
check the subscription, not the service.

## 6. Test it without emptying the tank

Push a fake low reading straight into Orion:

```bash
curl -iX PATCH http://localhost:1026/v2/entities/urn:ngsi-ld:Zone:001/attrs \
  -H "Content-Type: application/json" \
  -H "fiware-service: smartfarm" -H "fiware-servicepath: /" \
  -d '{"waterLevel": {"type": "Number", "value": 5}}'
```

`docker logs interlock` should show INTERLOCK ENGAGED and two valve closes.
Zone 1's next publish (within 120 s) overwrites the fake value with the real
one, and the interlock clears on its own once that is above 25.

## Notes

- Lockout state is in memory, hence one gunicorn worker. A restart re-evaluates
  on the next notification, at most ~120 s later.
- The service only ever *closes* valves. Reopening is left to each zone's own
  logic, so clearing the interlock never causes an unexpected watering.
- Zones 2 and 3 have no automatic irrigation yet, so today this only matters
  for manual commands. When they get the zone 1 controller, they should also
  consume `waterLevel` and gate locally rather than relying on this alone —
  a network round trip is a weak place to put pump protection.
