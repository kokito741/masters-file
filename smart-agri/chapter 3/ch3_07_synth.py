#!/usr/bin/env python3
"""
ch3_07_synth.py — генератор на синтетични данни с внесен дрейф и аномалии.

Това е самостоятелна разработка, а не помощен скрипт: върху краткосрочен
реален набор концептуален дрейф не настъпва, поради което ADWIN няма какво
да засече. Контролираното внасяне на изменение с ИЗВЕСТЕН момент е
единственият методически коректен начин да се оцени закъснението на
откриване и делът на погрешните установявания.

Модел на почвената влажност:
    m[t+1] = m[t] - k * ET(t) * gain + irrigation(t) + шум
където ET е приблизителна евапотранспирация от температура и влажност,
а gain е коефициент на сензора, който ДРЕЙФА след определен момент
(имитира отлагания по електродите).

Внесени аномалии: пикове, залепване на стойността и отпадания.

    pip install numpy pandas --break-system-packages
    python3 ch3_07_synth.py --zones 10 --days 120 --out synth.csv
"""
import argparse

import numpy as np
import pandas as pd

STAGES = [(0, "поникване"), (10, "вегетативен"), (30, "цъфтеж"), (55, "зреене")]


def et_proxy(temp, hum):
    """Опростен показател за изпарение: расте с температурата, пада с влажността."""
    return np.clip(0.35 + 0.045 * (temp - 18.0) - 0.010 * (hum - 45.0), 0.03, 3.0)


def generate_zone(zid, n, step_min, rng, drift_at, drift_span, anomaly_rate):
    t = pd.date_range("2026-03-01", periods=n, freq=f"{step_min}min", tz="UTC")
    hour = t.hour.values + t.minute.values / 60.0
    day = np.arange(n) * step_min / 1440.0

    # микроклимат: денонощен ход + сезонно затопляне + шум
    temp = (22 + 7 * np.sin((hour - 9) / 24 * 2 * np.pi)
            + 0.035 * day + rng.normal(0, 0.8, n))
    hum = np.clip(60 - 0.9 * (temp - 22) + rng.normal(0, 4, n), 12, 98)

    m_min = rng.uniform(28, 36)
    m_max = m_min + rng.uniform(12, 18)

    moisture = np.zeros(n)
    moisture[0] = rng.uniform(m_min + 3, m_max)
    irr_state = np.zeros(n, dtype=int)
    valve = np.zeros(n, dtype=int)
    flow = np.zeros(n)
    volume = np.zeros(n)

    # КОЕФИЦИЕНТ НА СЕНЗОРА — това е внесеният дрейф.
    # До drift_at е 1.0; след това нараства линейно за drift_span стъпки.
    gain = np.ones(n)
    if drift_at < n:
        end = min(n, drift_at + drift_span)
        gain[drift_at:end] = np.linspace(1.0, 1.6, end - drift_at)
        gain[end:] = 1.6

    soak_until = -1
    dose_left = 0.0
    for i in range(1, n):
        loss = 0.055 * et_proxy(temp[i], hum[i]) * gain[i] * (step_min / 10.0)
        gain_in = 0.0

        if dose_left > 0:                       # подава се доза
            rate = min(dose_left, 2.0 * step_min / 3.0)
            dose_left -= rate
            gain_in = rate * 5.5
            irr_state[i], valve[i] = 1, 1
            flow[i] = rate / (step_min / 60.0) if step_min else 0
            soak_until = i + int(30 / step_min)
        elif i < soak_until:
            irr_state[i] = 2
        elif (moisture[i - 1] < m_min and 5 <= hour[i] < 9):
            dose_left = 2.0
            irr_state[i], valve[i] = 1, 1

        volume[i] = volume[i - 1] + flow[i] * (step_min / 60.0)
        moisture[i] = np.clip(moisture[i - 1] - loss + gain_in + rng.normal(0, 0.25),
                              6, 95)

    measured = moisture.copy()
    is_anomaly = np.zeros(n, dtype=int)

    # аномалии с известни позиции
    n_anom = int(n * anomaly_rate)
    for idx in rng.choice(np.arange(50, n - 50), size=n_anom, replace=False):
        kind = rng.integers(0, 3)
        if kind == 0:                                  # пик
            measured[idx] += rng.choice([-1, 1]) * rng.uniform(18, 32)
            is_anomaly[idx] = 1
        elif kind == 1:                                # залепване
            L = int(rng.integers(4, 14))
            measured[idx:idx + L] = measured[idx]
            is_anomaly[idx:idx + L] = 1
        else:                                          # отпадане
            L = int(rng.integers(2, 8))
            measured[idx:idx + L] = np.nan
            is_anomaly[idx:idx + L] = 1

    stage = np.array([[s for d, s in STAGES if dd >= d][-1] for dd in day % 75])

    return pd.DataFrame({
        "entity_id": f"SynthZone:{zid:03d}",
        "time_index": t,
        "soilmoisture": np.round(measured, 2),
        "soilmoisture_true": np.round(moisture, 2),
        "airtemperature": np.round(temp, 2),
        "airhumidity": np.round(hum, 1),
        "soiltemperature": np.round(temp - 2.5 + rng.normal(0, .4, n), 2),
        "flowrate": np.round(flow, 3),
        "volumetotal": np.round(volume, 3),
        "valvestate": valve,
        "irrigationstate": irr_state,
        "moisturemin": m_min,
        "moisturemax": m_max,
        "stage": stage,
        "sensor_gain": np.round(gain, 3),
        "is_anomaly": is_anomaly,
        "drift_point": drift_at,
    })


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--zones", type=int, default=10)
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--step", type=int, default=10, help="минути между записите")
    p.add_argument("--drift-frac", type=float, default=0.55,
                   help="дял от реда, след който започва дрейфът")
    p.add_argument("--drift-days", type=float, default=12.0,
                   help="за колко дни се разгръща дрейфът")
    p.add_argument("--anomaly-rate", type=float, default=0.004)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="synth.csv")
    a = p.parse_args()

    n = int(a.days * 1440 / a.step)
    drift_at = int(n * a.drift_frac)
    drift_span = int(a.drift_days * 1440 / a.step)
    rng = np.random.default_rng(a.seed)

    parts = [generate_zone(z, n, a.step, rng, drift_at, drift_span, a.anomaly_rate)
             for z in range(1, a.zones + 1)]
    df = pd.concat(parts, ignore_index=True)
    df.to_csv(a.out, index=False)

    print(f"[out] {a.out}  —  {len(df)} реда, {a.zones} зони, {a.days} дни")
    print(f"[дрейф] започва на запис {drift_at} ({drift_at*a.step/1440:.1f} ден), "
          f"разгръща се за {a.drift_days} дни, коефициент 1.00 -> 1.60")
    print(f"[аномалии] {int(df['is_anomaly'].sum())} записа "
          f"({100*df['is_anomaly'].mean():.2f} %)")
    print("\nВАЖНО за текста: колоните soilmoisture_true, sensor_gain, is_anomaly и")
    print("drift_point са ИСТИНАТА и не се подават на моделите — служат само за оценка.")


if __name__ == "__main__":
    main()
