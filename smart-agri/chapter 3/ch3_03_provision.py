#!/usr/bin/env python3
"""
provision_zones.py — registers N synthetic zones on a FIWARE IoT Agent (UltraLight).

Deliberately separate from load_generator.py: provisioning is slow, one-off and
idempotent; measurement publishing is fast and repeated. Mixing them would put
several minutes of HTTP setup inside the window you are trying to time.

Two known IoT Agent 1.21 pitfalls are handled explicitly here:
  * the service group carries resource="/iot/d" even though transport is MQTT
    (resource="" silently fails with srv=n/a and MEASURES-004)
  * every device record carries protocol="PDI-IoTA-UltraLight"
    (missing protocol also produces MEASURES-004 on an existing device)

Usage:
    python3 provision_zones.py --count 100
    python3 provision_zones.py --count 100 --delete
    python3 provision_zones.py --count 100 --iota http://192.168.0.164:4041
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

DEFAULT_IOTA = "http://192.168.0.164:4041"
DEFAULT_CBROKER = "http://orion:1026"
DEFAULT_APIKEY = "synthkey"   # НЕ реалния ключ: синтетичните зони се държат отделно
DEFAULT_SERVICE = "smartfarm"
DEFAULT_PATH = "/"
ENTITY_TYPE = "SynthZone"

# UltraLight object_id -> NGSI attribute. Keep object_ids short: they are sent
# on every single message and inflate broker throughput at high N.
ATTRIBUTES = [
    {"object_id": "h", "name": "soilMoisture", "type": "Number"},
    {"object_id": "t", "name": "soilTemperature", "type": "Number"},
    {"object_id": "a", "name": "airTemperature", "type": "Number"},
    {"object_id": "u", "name": "airHumidity", "type": "Number"},
    {"object_id": "sq", "name": "seq", "type": "Number"},
]


def headers(service, path):
    return {
        "Content-Type": "application/json",
        "fiware-service": service,
        "fiware-servicepath": path,
    }


def device_id(i):
    return f"synth{i:04d}"


def entity_name(i):
    return f"{ENTITY_TYPE}:{i:04d}"


def ensure_service_group(args):
    """Create the service group. 409 means it already exists, which is fine."""
    body = {
        "services": [
            {
                "apikey": args.apikey,
                "cbroker": args.cbroker,
                "entity_type": ENTITY_TYPE,
                "resource": "/iot/d",  # required even for MQTT transport
            }
        ]
    }
    try:
        r = requests.post(
            f"{args.iota}/iot/services",
            headers=headers(args.service, args.path),
            data=json.dumps(body),
            timeout=15,
        )
    except requests.exceptions.RequestException as exc:
        sys.exit(f"[group] IoT Agent unreachable at {args.iota}: {exc.__class__.__name__}")
    if r.status_code in (201, 409):
        print(f"[group] apikey={args.apikey} -> HTTP {r.status_code}")
        return
    sys.exit(f"[group] provisioning failed: HTTP {r.status_code} {r.text}")


def build_device(i):
    return {
        "device_id": device_id(i),
        "entity_name": entity_name(i),
        "entity_type": ENTITY_TYPE,
        "transport": "MQTT",
        "protocol": "PDI-IoTA-UltraLight",  # required, see module docstring
        "attributes": ATTRIBUTES,
        "static_attributes": [
            {"name": "synthetic", "type": "Boolean", "value": True},
            {"name": "zoneIndex", "type": "Number", "value": i},
        ],
    }


def post_batch(args, batch):
    r = requests.post(
        f"{args.iota}/iot/devices",
        headers=headers(args.service, args.path),
        data=json.dumps({"devices": batch}),
        timeout=60,
    )
    if r.status_code == 201:
        return len(batch), 0, None
    if r.status_code == 409:
        return 0, len(batch), None
    return 0, 0, f"HTTP {r.status_code} {r.text[:200]}"


def delete_one(args, i):
    r = requests.delete(
        f"{args.iota}/iot/devices/{device_id(i)}",
        headers=headers(args.service, args.path),
        timeout=30,
    )
    return r.status_code in (204, 404)


def provision(args):
    ensure_service_group(args)
    devices = [build_device(i) for i in range(1, args.count + 1)]
    batches = [
        devices[i : i + args.batch_size]
        for i in range(0, len(devices), args.batch_size)
    ]

    created = skipped = 0
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(post_batch, args, b) for b in batches]
        for done, fut in enumerate(as_completed(futures), 1):
            c, s, err = fut.result()
            created += c
            skipped += s
            if err:
                errors.append(err)
            print(f"\r[devices] batch {done}/{len(batches)}", end="", flush=True)

    print(f"\n[devices] created={created} already_present={skipped} errors={len(errors)}")
    for e in errors[:5]:
        print(f"  ! {e}")
    if errors:
        sys.exit(1)


def teardown(args):
    ok = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(delete_one, args, i) for i in range(1, args.count + 1)]
        for done, fut in enumerate(as_completed(futures), 1):
            ok += 1 if fut.result() else 0
            print(f"\r[delete] {done}/{args.count}", end="", flush=True)
    print(f"\n[delete] removed_or_absent={ok}/{args.count}")
    print("Note: Orion entities are not deleted by the agent. To clear them:")
    print(f'  for i in $(seq -w 1 {args.count}); do')
    print(f'    curl -X DELETE "$ORION/v2/entities/{ENTITY_TYPE}:$i" \\')
    print(f'      -H "fiware-service: {args.service}" -H "fiware-servicepath: {args.path}"')
    print("  done")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, required=True, help="number of synthetic zones")
    p.add_argument("--iota", default=DEFAULT_IOTA)
    p.add_argument("--cbroker", default=DEFAULT_CBROKER,
                   help="broker URL as the AGENT sees it (container name, not LAN IP)")
    p.add_argument("--apikey", default=DEFAULT_APIKEY)
    p.add_argument("--service", default=DEFAULT_SERVICE)
    p.add_argument("--path", default=DEFAULT_PATH)
    p.add_argument("--batch-size", type=int, default=25)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--delete", action="store_true", help="tear down instead of create")
    args = p.parse_args()

    if args.delete:
        teardown(args)
    else:
        provision(args)


if __name__ == "__main__":
    main()
