#!/usr/bin/env python3
"""
ch3_02_quality.py — анализ за раздели 3.2, 3.3 и 3.4.

Произвежда:
  * tab_3_1_quality.csv      обем и качество на данните по зони      (3.2)
  * tab_3_2_stats.csv        описателна статистика по величини       (3.3)
  * tab_3_3_irrigation.csv   отделните напоителни събития            (3.4)
  * fig_3_1_moisture.png     почвена влажност по зони
  * fig_3_2_gaps.png         прекъсвания в данните
  * fig_3_3_irrigation.png   цикъл напояване-попиване

    pip install pandas matplotlib --break-system-packages
    python3 ch3_02_quality.py --csv zones.csv
"""
import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

VALUES = ["soilmoisture", "soiltemperature", "airtemperature", "airhumidity",
          "soilconductivity", "soilph", "waterlevel"]

# Състояния на напоителния автомат във фърмуера
IRR = {0: "IDLE", 1: "WATERING", 2: "SOAKING", 3: "LOCKOUT", 4: "MANUAL"}


def quality(df, expected_min):
    """Обем, непрекъснатост и липсващи стойности по зони — раздел 3.2."""
    rows = []
    for zid, g in df.groupby("entity_id"):
        g = g.sort_values("time_index")
        span_min = (g["time_index"].max() - g["time_index"].min()).total_seconds() / 60
        gaps = g["time_index"].diff().dt.total_seconds().div(60)
        expected = span_min / expected_min if expected_min else float("nan")
        rows.append({
            "зона": zid,
            "редове": len(g),
            "от": g["time_index"].min().strftime("%Y-%m-%d %H:%M"),
            "до": g["time_index"].max().strftime("%Y-%m-%d %H:%M"),
            "период_дни": round(span_min / 1440, 2),
            "очаквани_редове": int(expected) if expected == expected else None,
            "пълнота_%": round(100 * len(g) / expected, 1) if expected and expected == expected else None,
            "меден_интервал_min": round(gaps.median(), 1),
            "макс_прекъсване_min": round(gaps.max(), 1),
            "прекъсвания_над_3x": int((gaps > 3 * expected_min).sum()) if expected_min else None,
            "липсващи_влажност_%": round(100 * g["soilmoisture"].isna().mean(), 1),
        })
    return pd.DataFrame(rows)


def stats(df):
    """Описателна статистика по величини и зони — раздел 3.3."""
    out = []
    for zid, g in df.groupby("entity_id"):
        for col in VALUES:
            s = pd.to_numeric(g[col], errors="coerce").dropna()
            if s.empty:
                continue
            out.append({
                "зона": zid, "величина": col, "n": len(s),
                "средно": round(s.mean(), 2), "ст_откл": round(s.std(), 2),
                "мин": round(s.min(), 2), "p05": round(s.quantile(.05), 2),
                "медиана": round(s.median(), 2), "p95": round(s.quantile(.95), 2),
                "макс": round(s.max(), 2),
            })
    return pd.DataFrame(out)


def irrigation_events(df):
    """Отделя напоителните събития по преходите на автомата — раздел 3.4."""
    events = []
    for zid, g in df.groupby("entity_id"):
        g = g.sort_values("time_index").reset_index(drop=True)
        st = pd.to_numeric(g["irrigationstate"], errors="coerce").fillna(-1).astype(int)
        vol = pd.to_numeric(g["volumetotal"], errors="coerce")
        moist = pd.to_numeric(g["soilmoisture"], errors="coerce")
        flow = pd.to_numeric(g["flowrate"], errors="coerce")

        start = None
        for i in range(1, len(g)):
            entering = st[i] == 1 and st[i - 1] != 1
            leaving = st[i] != 1 and st[i - 1] == 1
            if entering:
                start = i
            elif leaving and start is not None:
                dur = (g.loc[i, "time_index"] - g.loc[start, "time_index"]).total_seconds() / 60
                delivered = vol[i] - vol[start] if pd.notna(vol[i]) and pd.notna(vol[start]) else None
                # влажност 45 min по-късно = след периода на попиване
                after = g[g["time_index"] >= g.loc[i, "time_index"] + pd.Timedelta(minutes=45)]
                m_after = pd.to_numeric(after["soilmoisture"], errors="coerce").iloc[0] \
                    if len(after) else None
                events.append({
                    "зона": zid,
                    "начало": g.loc[start, "time_index"].strftime("%Y-%m-%d %H:%M"),
                    "продълж_min": round(dur, 1),
                    "подаден_обем_L": round(delivered, 3) if delivered is not None else None,
                    "среден_дебит_Lmin": round(flow[start:i + 1].mean(), 2),
                    "влажност_преди_%": round(moist[start], 1) if pd.notna(moist[start]) else None,
                    "влажност_след_45min_%": round(m_after, 1) if m_after is not None else None,
                    "прираст_пункта": round(m_after - moist[start], 1)
                    if m_after is not None and pd.notna(moist[start]) else None,
                    "следващо_състояние": IRR.get(int(st[i]), str(st[i])),
                })
                start = None
    return pd.DataFrame(events)


def plots(df, ev):
    zones = sorted(df["entity_id"].unique())

    # фиг. 3.1 — влажност по зони с праговете
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for z in zones:
        g = df[df["entity_id"] == z].sort_values("time_index")
        ax.plot(g["time_index"], pd.to_numeric(g["soilmoisture"], errors="coerce"),
                lw=1.1, label=z.split(":")[-1])
        mn = pd.to_numeric(g["moisturemin"], errors="coerce")
        if mn.notna().any():
            ax.plot(g["time_index"], mn, lw=0.8, ls="--", alpha=.5)
    ax.set_ylabel("почвена влажност, %")
    ax.set_xlabel("време")
    ax.legend(title="зона", fontsize=8)
    ax.grid(alpha=.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig("fig_3_1_moisture.png", dpi=160)
    plt.close(fig)

    # фиг. 3.2 — разпределение на интервалите между измерванията
    fig, ax = plt.subplots(figsize=(9, 4))
    for z in zones:
        g = df[df["entity_id"] == z].sort_values("time_index")
        d = g["time_index"].diff().dt.total_seconds().div(60).dropna()
        d = d[d < d.quantile(.995)]
        ax.hist(d, bins=60, alpha=.55, label=z.split(":")[-1])
    ax.set_xlabel("интервал между съседни измервания, min")
    ax.set_ylabel("брой")
    ax.legend(title="зона", fontsize=8)
    ax.grid(alpha=.3)
    fig.tight_layout()
    fig.savefig("fig_3_2_gaps.png", dpi=160)
    plt.close(fig)

    # фиг. 3.3 — най-дългото напоително събитие в детайл
    if not ev.empty:
        top = ev.sort_values("продълж_min", ascending=False).iloc[0]
        z = top["зона"]
        t0 = pd.Timestamp(top["начало"], tz="UTC")
        g = df[(df["entity_id"] == z) &
               (df["time_index"] >= t0 - pd.Timedelta(minutes=30)) &
               (df["time_index"] <= t0 + pd.Timedelta(minutes=120))].sort_values("time_index")
        if len(g) > 3:
            fig, ax1 = plt.subplots(figsize=(10, 4.2))
            ax1.plot(g["time_index"], pd.to_numeric(g["soilmoisture"], errors="coerce"),
                     lw=1.6, label="влажност, %")
            ax1.set_ylabel("почвена влажност, %")
            ax2 = ax1.twinx()
            ax2.plot(g["time_index"], pd.to_numeric(g["flowrate"], errors="coerce"),
                     lw=1.2, ls="--", color="grey", label="дебит, L/min")
            ax2.set_ylabel("дебит, L/min")
            ax1.set_title(f"Цикъл напояване-попиване, {z}")
            ax1.grid(alpha=.3)
            fig.autofmt_xdate()
            fig.tight_layout()
            fig.savefig("fig_3_3_irrigation.png", dpi=160)
            plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default="zones.csv")
    p.add_argument("--interval", type=float, default=2.0,
                   help="очакван интервал на публикуване в минути (по подразбиране 2)")
    a = p.parse_args()

    df = pd.read_csv(a.csv)
    df["time_index"] = pd.to_datetime(df["time_index"], format="ISO8601", utc=True)
    df = df.dropna(subset=["time_index"]).sort_values(["entity_id", "time_index"])

    q = quality(df, a.interval)
    q.to_csv("tab_3_1_quality.csv", index=False)
    print("\n=== Табл. 3.1 Обем и качество на данните ===")
    print(q.to_string(index=False))

    s = stats(df)
    s.to_csv("tab_3_2_stats.csv", index=False)
    print("\n=== Табл. 3.2 Описателна статистика (извадка) ===")
    print(s[s["величина"] == "soilmoisture"].to_string(index=False))

    ev = irrigation_events(df)
    ev.to_csv("tab_3_3_irrigation.csv", index=False)
    print(f"\n=== Табл. 3.3 Напоителни събития: {len(ev)} ===")
    if not ev.empty:
        print(ev.head(12).to_string(index=False))
        print("\n[обобщение]")
        print(f"  среден подаден обем : {ev['подаден_обем_L'].mean():.3f} L")
        print(f"  средна продължителност: {ev['продълж_min'].mean():.1f} min")
        pr = ev["прираст_пункта"].dropna()
        if len(pr):
            print(f"  среден прираст на влажността след попиване: {pr.mean():.1f} пункта")
        dry = (ev["следващо_състояние"] == "LOCKOUT").sum()
        print(f"  събития, завършили в LOCKOUT (сух ход / таймаут): {dry}")

    plots(df, ev)
    print("\n[out] fig_3_1_moisture.png  fig_3_2_gaps.png  fig_3_3_irrigation.png")


if __name__ == "__main__":
    main()
