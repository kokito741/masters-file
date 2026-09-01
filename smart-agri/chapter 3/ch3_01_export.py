#!/usr/bin/env python3
"""
ch3_01_export.py — изнася реалните измервания от CrateDB в CSV.

Този CSV е входът за ch3_02_quality.py и за ch3_08_ml_batch.py.
Изнася се веднъж и се използва многократно, за да не се натоварва базата
при всяко изпълнение на анализа.

    pip install requests pandas --break-system-packages
    python3 ch3_01_export.py --out zones.csv
    python3 ch3_01_export.py --out zones.csv --days 30
"""
import argparse
import sys

import pandas as pd
import requests

COLS = [
    "entity_id", "time_index", "soilmoisture", "soiltemperature",
    "airtemperature", "airhumidity", "soilconductivity", "soilph",
    "flowrate", "volumetotal", "valvestate", "pumpstate",
    "waterlevel", "irrigationstate", "moisturemin", "moisturemax",
]


def sql(crate, stmt, args=None):
    r = requests.post(f"{crate}/_sql", json={"stmt": stmt, "args": args or []}, timeout=120)
    if r.status_code != 200:
        sys.exit(f"CrateDB върна HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--crate", default="http://192.168.0.164:4200")
    p.add_argument("--schema", default="mtsmartfarm")
    p.add_argument("--table", default="etzone")
    p.add_argument("--days", type=int, default=0, help="0 = всички данни")
    p.add_argument("--out", default="zones.csv")
    a = p.parse_args()

    where = ""
    args = []
    if a.days > 0:
        where = "WHERE time_index >= now() - INTERVAL '%d days'" % a.days

    stmt = (f'SELECT {", ".join(COLS)} FROM "{a.schema}"."{a.table}" '
            f'{where} ORDER BY entity_id, time_index')
    print(f"[sql] {stmt[:110]}...")

    res = sql(a.crate, stmt, args)
    df = pd.DataFrame(res["rows"], columns=[c["name"] if isinstance(c, dict) else c
                                            for c in res.get("cols", COLS)])
    if df.empty:
        sys.exit("Няма върнати редове. Проверете схемата и таблицата (ch3_00_prepare.sh, стъпка 4).")

    df["time_index"] = pd.to_datetime(df["time_index"], unit="ms", utc=True)
    df = df.sort_values(["entity_id", "time_index"]).reset_index(drop=True)
    df.to_csv(a.out, index=False)

    print(f"\n[out] {a.out}  —  {len(df)} реда")
    print(f"[период] {df['time_index'].min()}  ->  {df['time_index'].max()}")
    print("\n[редове по зони]")
    print(df.groupby("entity_id").size().to_string())
    print("\n[дял на липсващите стойности, %]")
    print((df[COLS[2:]].isna().mean() * 100).round(1).to_string())


if __name__ == "__main__":
    main()
