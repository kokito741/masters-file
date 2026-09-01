#!/usr/bin/env python3
"""
ch3_06_sweep_report.py — обобщава пробезите от ch3_05_sweep.sh (раздел 3.6).

Произвежда:
  * tab_3_4_scalability.csv  закъснения и загуби по брой зони
  * fig_3_4_latency.png      закъснение до Orion и до CrateDB
  * fig_3_5_backlog.png      задръстване на историческия път
  * fig_3_6_timeouts.png     дял на непристигналите проби

Определя тясното място по правилото: ако t_orion остава устойчиво, докато
backlog нараства неограничено, ограничаващият участък е историческият път,
а не контекстният брокер.

    python3 ch3_06_sweep_report.py
"""
import glob
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def load():
    rows = []
    for f in sorted(glob.glob("*-summary.json")):
        with open(f) as fh:
            s = json.load(fh)
        rows.append({
            "режим": "без разсейване" if s["run_id"].startswith("lockstep") else "разсеян",
            "зони": s["zones"],
            "връзки": s["connections"],
            "интервал_s": s["interval_s"],
            "публикувани": s["messages_published"],
            "проби": s["probes_completed"],
            "таймаути": s["probe_timeouts"],
            "загуби_%": round(100 * s["probe_timeouts"] / max(1, s["probes_completed"]), 1),
            "publish_p50": s["t_publish"].get("p50_ms"),
            "publish_p95": s["t_publish"].get("p95_ms"),
            "orion_p50": s["t_orion"].get("p50_ms"),
            "orion_p95": s["t_orion"].get("p95_ms"),
            "orion_p99": s["t_orion"].get("p99_ms"),
            "crate_p50": s["t_crate"].get("p50_ms"),
            "crate_p95": s["t_crate"].get("p95_ms"),
            "crate_p99": s["t_crate"].get("p99_ms"),
            "backlog_p50": s["backlog"].get("p50_ms"),
            "backlog_p95": s["backlog"].get("p95_ms"),
            "разделителна_ms": s["poll_resolution_ms"],
        })
    return pd.DataFrame(rows).sort_values(["режим", "зони"])


def verdict(d):
    """Определя ограничаващия участък по поведението на двете криви."""
    if len(d) < 3:
        return "недостатъчно точки за заключение"
    o = d["orion_p95"].astype(float)
    b = d["backlog_p95"].astype(float)
    o_growth = o.iloc[-1] / max(o.iloc[0], 1e-9)
    b_growth = b.iloc[-1] / max(b.iloc[0], 1e-9)
    if b_growth > 3 * o_growth and b_growth > 3:
        return (f"историческият път (backlog нараства {b_growth:.1f}x срещу "
                f"{o_growth:.1f}x за Orion) — QuantumLeap/CrateDB е тясното място")
    if o_growth > 3:
        return (f"контекстният брокер (t_orion нараства {o_growth:.1f}x) — "
                f"Orion/MongoDB е тясното място")
    return f"без насищане в изпитания обхват (Orion {o_growth:.1f}x, backlog {b_growth:.1f}x)"


def main():
    d = load()
    if d.empty:
        raise SystemExit("Няма файлове *-summary.json. Пуснете първо ch3_05_sweep.sh")

    d.to_csv("tab_3_4_scalability.csv", index=False)
    print("=== Табл. 3.4 Мащабируемост ===")
    cols = ["режим", "зони", "публикувани", "проби", "загуби_%",
            "orion_p50", "orion_p95", "crate_p50", "crate_p95", "backlog_p95"]
    print(d[cols].to_string(index=False))

    j = d[d["режим"] == "разсеян"].sort_values("зони")
    print("\n[заключение] ограничаващ участък: " + verdict(j))
    print(f"[бележка] разделителна способност на измерването: "
          f"{d['разделителна_ms'].iloc[0]:.0f} ms — посочете я в текста")

    if len(j) >= 2:
        # фиг. 3.4 — закъснения
        fig, ax = plt.subplots(figsize=(9, 4.5))
        ax.plot(j["зони"], j["orion_p50"], "o-", label="до Orion, p50")
        ax.plot(j["зони"], j["orion_p95"], "o--", label="до Orion, p95")
        ax.plot(j["зони"], j["crate_p50"], "s-", label="до CrateDB, p50")
        ax.plot(j["зони"], j["crate_p95"], "s--", label="до CrateDB, p95")
        ax.set_xlabel("брой зони")
        ax.set_ylabel("закъснение, ms")
        ax.set_yscale("log")
        ax.grid(alpha=.3, which="both")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig("fig_3_4_latency.png", dpi=160)
        plt.close(fig)

        # фиг. 3.5 — задръстване
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(j["зони"], j["backlog_p50"], "o-", label="p50")
        ax.plot(j["зони"], j["backlog_p95"], "o--", label="p95")
        ax.set_xlabel("брой зони")
        ax.set_ylabel("t_crate − t_orion, ms")
        ax.set_title("Задръстване на пътя на уведомленията")
        ax.grid(alpha=.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig("fig_3_5_backlog.png", dpi=160)
        plt.close(fig)

        # фиг. 3.6 — загуби
        fig, ax = plt.subplots(figsize=(8, 3.8))
        ax.bar(j["зони"].astype(str), j["загуби_%"])
        ax.set_xlabel("брой зони")
        ax.set_ylabel("непристигнали проби, %")
        ax.grid(alpha=.3, axis="y")
        fig.tight_layout()
        fig.savefig("fig_3_6_timeouts.png", dpi=160)
        plt.close(fig)

        print("\n[out] fig_3_4_latency.png  fig_3_5_backlog.png  fig_3_6_timeouts.png")


if __name__ == "__main__":
    main()
