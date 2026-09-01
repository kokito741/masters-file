#!/usr/bin/env python3
"""
load_generator.py — synthetic measurement load against the FIWARE stack.

Assumes provision_zones.py has already registered the devices.

WHY THREE METRICS AND NOT ONE
    A single "the system held N devices" number is unusable in a thesis because
    it does not say what broke. The pipeline has two independent stages that
    saturate for different reasons:

        t_orion  = publish -> value visible in Orion
                   (broker + IoT Agent + Orion + MongoDB write)
        t_crate  = publish -> row visible in CrateDB
        backlog  = t_crate - t_orion
                   (Orion notification + QuantumLeap + CrateDB write)

    If t_orion stays flat while backlog grows without bound, the bottleneck is
    the historical path, not the context broker — QuantumLeap is falling behind
    and the notification queue is filling. That is the expected failure mode and
    it is a different chapter conclusion from "Orion saturated".

    t_publish (publish -> QoS 1 PUBACK) is measured separately so broker-level
    saturation can be excluded before blaming anything downstream.

METHOD NOTE FOR CHAPTER III
    Probe messages are polled from this process, so every timestamp comes off
    one clock. No NTP skew between the ESP32s and the server enters the figures.
    The cost is a quantisation error equal to --poll-interval; report it.

Usage:
    python3 load_generator.py --count 25 --duration 300
    python3 load_generator.py --count 100 --duration 600 --clients 100 --docker-stats
"""

import argparse
import csv
import json
import math
import queue
import random
import statistics
import subprocess
import sys
import threading
import time
import uuid

import requests

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("pip install paho-mqtt requests")

ENTITY_TYPE = "SynthZone"
_stop = threading.Event()


# ----------------------------------------------------------------- helpers

def new_client(client_id):
    """paho-mqtt 1.x and 2.x have incompatible constructors."""
    try:
        from paho.mqtt.client import CallbackAPIVersion
        return mqtt.Client(CallbackAPIVersion.VERSION1, client_id=client_id)
    except (ImportError, AttributeError):
        return mqtt.Client(client_id=client_id)


def percentile(values, p):
    vals = sorted(v for v in values if v is not None and not math.isnan(v))
    if not vals:
        return float("nan")
    k = (len(vals) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def summarise(values):
    clean = [v for v in values if v is not None and not math.isnan(v)]
    if not clean:
        return {"n": 0}
    return {
        "n": len(clean),
        "mean_ms": round(statistics.fmean(clean), 1),
        "p50_ms": round(percentile(clean, 50), 1),
        "p95_ms": round(percentile(clean, 95), 1),
        "p99_ms": round(percentile(clean, 99), 1),
        "max_ms": round(max(clean), 1),
    }


def device_id(i):
    return f"synth{i:04d}"


def entity_id(i):
    return f"{ENTITY_TYPE}:{i:04d}"


# ----------------------------------------------------------------- publishing

class Publisher(threading.Thread):
    """Owns one MQTT connection and the devices assigned to it."""

    def __init__(self, args, indices, probe_q, results):
        super().__init__(daemon=True)
        self.args = args
        self.indices = indices
        self.probe_q = probe_q
        self.results = results
        self.client = new_client(f"loadgen-{uuid.uuid4().hex[:8]}")
        self.seq = {i: 0 for i in indices}
        self.state = {i: random.uniform(30.0, 60.0) for i in indices}

    def run(self):
        try:
            self.client.connect(self.args.broker, self.args.port, keepalive=60)
        except Exception as exc:
            print(f"[publisher] connect failed: {exc}")
            _stop.set()
            return
        self.client.loop_start()

        # Spread device phases so they do not all fire on the same tick.
        offsets = {i: random.uniform(0, self.args.interval) for i in self.indices}
        start = time.monotonic()

        while not _stop.is_set():
            now = time.monotonic() - start
            for i in self.indices:
                due = offsets[i]
                if now >= due:
                    self.publish_one(i)
                    step = self.args.interval
                    if self.args.jitter:
                        step *= random.uniform(0.85, 1.15)
                    offsets[i] = due + step
            time.sleep(0.01)

        self.client.loop_stop()
        self.client.disconnect()

    def publish_one(self, i):
        self.seq[i] += 1
        seq = self.seq[i]

        # Plausible drying curve so the payloads are not constant.
        self.state[i] = max(8.0, self.state[i] - random.uniform(0.0, 0.35))
        if self.state[i] < 25.0 and random.random() < 0.25:
            self.state[i] = random.uniform(55.0, 70.0)  # irrigation event

        payload = (
            f"h|{self.state[i]:.1f}"
            f"|t|{random.uniform(17, 26):.1f}"
            f"|a|{random.uniform(18, 30):.1f}"
            f"|u|{random.uniform(35, 80):.1f}"
            f"|sq|{seq}"
        )
        topic = f"/{self.args.apikey}/{device_id(i)}/attrs"

        t0 = time.monotonic()
        info = self.client.publish(topic, payload, qos=1)
        is_probe = (seq % self.args.probe_every == 0)
        try:
            info.wait_for_publish(timeout=10)
            t_publish = (time.monotonic() - t0) * 1000.0
        except Exception:
            t_publish = float("nan")

        self.results["publish"].append(t_publish)
        if is_probe:
            self.probe_q.put((i, seq, t0, t_publish))


# ----------------------------------------------------------------- probing

class Prober(threading.Thread):
    """Follows probe messages through Orion and then CrateDB."""

    def __init__(self, args, probe_q, rows, results):
        super().__init__(daemon=True)
        self.args = args
        self.probe_q = probe_q
        self.rows = rows
        self.results = results
        self.session = requests.Session()
        self.headers = {
            "fiware-service": args.service,
            "fiware-servicepath": args.path,
        }

    def run(self):
        while not (_stop.is_set() and self.probe_q.empty()):
            try:
                i, seq, t0, t_publish = self.probe_q.get(timeout=0.5)
            except queue.Empty:
                continue
            self.track(i, seq, t0, t_publish)

    def track(self, i, seq, t0, t_publish):
        deadline = t0 + self.args.probe_timeout
        t_orion = self.wait_orion(i, seq, t0, deadline)
        t_crate = self.wait_crate(i, seq, t0, deadline)
        backlog = (
            t_crate - t_orion
            if not (math.isnan(t_orion) or math.isnan(t_crate))
            else float("nan")
        )
        self.results["orion"].append(t_orion)
        self.results["crate"].append(t_crate)
        self.results["backlog"].append(backlog)
        self.rows.append(
            {
                "wall_clock": round(time.time(), 3),
                "entity_id": entity_id(i),
                "seq": seq,
                "t_publish_ms": round(t_publish, 1) if not math.isnan(t_publish) else "",
                "t_orion_ms": round(t_orion, 1) if not math.isnan(t_orion) else "",
                "t_crate_ms": round(t_crate, 1) if not math.isnan(t_crate) else "",
                "backlog_ms": round(backlog, 1) if not math.isnan(backlog) else "",
            }
        )

    def wait_orion(self, i, seq, t0, deadline):
        url = f"{self.args.orion}/v2/entities/{entity_id(i)}/attrs/seq/value"
        while time.monotonic() < deadline:
            try:
                r = self.session.get(url, headers=self.headers, timeout=5)
                if r.status_code == 200 and float(r.text) >= seq:
                    return (time.monotonic() - t0) * 1000.0
            except Exception:
                pass
            time.sleep(self.args.poll_interval)
        return float("nan")

    def wait_crate(self, i, seq, t0, deadline):
        stmt = (
            f'SELECT time_index FROM "{self.args.crate_schema}".'
            f'"{self.args.crate_table}" WHERE entity_id = ? AND seq >= ? LIMIT 1'
        )
        body = {"stmt": stmt, "args": [entity_id(i), seq]}
        while time.monotonic() < deadline:
            try:
                r = self.session.post(
                    f"{self.args.crate}/_sql", json=body, timeout=10
                )
                if r.status_code == 200 and r.json().get("rowcount", 0) > 0:
                    return (time.monotonic() - t0) * 1000.0
            except Exception:
                pass
            time.sleep(self.args.poll_interval)
        return float("nan")


# ----------------------------------------------------------------- resources

def sample_resources(path, interval):
    """Optional container CPU/RAM sampling. Only works where docker runs."""
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["wall_clock", "container", "cpu_pct", "mem_usage", "mem_pct"])
        while not _stop.is_set():
            try:
                out = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                    capture_output=True, text=True, timeout=30,
                )
                ts = round(time.time(), 3)
                for line in out.stdout.strip().splitlines():
                    d = json.loads(line)
                    w.writerow([ts, d.get("Name"), d.get("CPUPerc"),
                                d.get("MemUsage"), d.get("MemPerc")])
                fh.flush()
            except Exception as exc:
                print(f"[resources] {exc}")
                return
            time.sleep(interval)


# ----------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--count", type=int, required=True, help="number of zones to drive")
    p.add_argument("--duration", type=int, default=300, help="seconds")
    p.add_argument("--interval", type=float, default=10.0,
                   help="seconds between measurements per zone")
    p.add_argument("--clients", type=int, default=0,
                   help="TCP connections; 0 = one per 10 zones. Report this: "
                        "100 zones on one connection is not 100 ESP32s.")
    p.add_argument("--jitter", action="store_true",
                   help="vary intervals; without it all zones fire in lockstep "
                        "(worst case, also worth measuring)")
    p.add_argument("--probe-every", type=int, default=10,
                   help="track every Nth message end to end")
    p.add_argument("--probe-timeout", type=float, default=90.0)
    p.add_argument("--poll-interval", type=float, default=0.1,
                   help="polling resolution; this is your measurement error")
    p.add_argument("--probers", type=int, default=8)

    p.add_argument("--broker", default="192.168.0.164")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--apikey", default="synthkey")
    p.add_argument("--orion", default="http://192.168.0.164:1026")
    p.add_argument("--crate", default="http://192.168.0.164:4200")
    p.add_argument("--service", default="smartfarm")
    p.add_argument("--path", default="/")
    p.add_argument("--crate-schema", default="mtsmartfarm",
                   help="провери с ch3_00_prepare.sh, стъпка 4")
    p.add_argument("--crate-table", default="etsynthzone")
    p.add_argument("--docker-stats", action="store_true")
    p.add_argument("--out-prefix", default="run")
    args = p.parse_args()

    run_id = f"{args.out_prefix}-n{args.count}-{time.strftime('%Y%m%d-%H%M%S')}"
    n_clients = args.clients or max(1, args.count // 10)
    indices = list(range(1, args.count + 1))
    buckets = [indices[c::n_clients] for c in range(n_clients)]

    probe_q = queue.Queue()
    rows = []
    results = {"publish": [], "orion": [], "crate": [], "backlog": []}

    print(f"[run] {run_id}  zones={args.count}  connections={n_clients}  "
          f"interval={args.interval}s  duration={args.duration}s")

    threads = [Publisher(args, b, probe_q, results) for b in buckets if b]
    threads += [Prober(args, probe_q, rows, results) for _ in range(args.probers)]
    if args.docker_stats:
        threads.append(threading.Thread(
            target=sample_resources, args=(f"{run_id}-resources.csv", 5), daemon=True))

    for t in threads:
        t.start()

    try:
        end = time.monotonic() + args.duration
        while time.monotonic() < end:
            time.sleep(2)
            print(f"\r[run] probes_done={len(rows)} pending={probe_q.qsize()}",
                  end="", flush=True)
    except KeyboardInterrupt:
        print("\n[run] interrupted")
    finally:
        _stop.set()
        print("\n[run] draining probes...")
        for t in threads:
            t.join(timeout=args.probe_timeout + 10)

    # ---- output
    csv_path = f"{run_id}-probes.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "wall_clock", "entity_id", "seq",
            "t_publish_ms", "t_orion_ms", "t_crate_ms", "backlog_ms"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["wall_clock"]))

    timeouts = sum(1 for r in rows if r["t_crate_ms"] == "")
    summary = {
        "run_id": run_id,
        "zones": args.count,
        "connections": n_clients,
        "interval_s": args.interval,
        "jitter": args.jitter,
        "duration_s": args.duration,
        "poll_resolution_ms": args.poll_interval * 1000,
        "messages_published": len(results["publish"]),
        "probes_completed": len(rows),
        "probe_timeouts": timeouts,
        "t_publish": summarise(results["publish"]),
        "t_orion": summarise(results["orion"]),
        "t_crate": summarise(results["crate"]),
        "backlog": summarise(results["backlog"]),
    }
    with open(f"{run_id}-summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)

    print(json.dumps(summary, indent=2))
    print(f"\n[out] {csv_path}\n[out] {run_id}-summary.json")
    if timeouts:
        print(f"[warn] {timeouts} probes never reached CrateDB — "
              f"at N={args.count} the historical path is saturated.")


if __name__ == "__main__":
    main()
