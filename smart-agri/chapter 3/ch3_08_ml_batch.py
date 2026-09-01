#!/usr/bin/env python3
"""
ch3_08_ml_batch.py — пакетен модел (раздел 3.5.2).

Задача: ще спадне ли почвената влажност под moistureMin в следващите H часа.
Класификация, защото това е решението, което системата действително взема.

Разделянето е ПО ВРЕМЕ, не случайно: случайното разделяне при времеви ред
пропуска бъдеща информация в обучаващото множество и дава подвеждащо високи
показатели.

    pip install lightgbm scikit-learn pandas numpy --break-system-packages
    python3 ch3_08_ml_batch.py --csv synth.csv --tag synth
    python3 ch3_08_ml_batch.py --csv zones.csv --tag real
"""
import argparse
import json

import numpy as np
import pandas as pd

FEATURES = ["m", "m_d1", "m_d6", "m_mean_1h", "m_std_3h", "m_min_6h",
            "at", "ah", "at_mean_3h", "vpd", "hour_sin", "hour_cos",
            "mmin", "margin", "irr"]


def detect_step(df):
    """Открива действителния интервал между записите в минути.

    Задължително: хоризонтът и прозорците се смятат в БРОЙ ЗАПИСИ. Ако
    приетият интервал не съвпада с действителния, всички прозорци се
    изкривяват мълчаливо — 6-часов хоризонт при приет 10 min и действителни
    2 min се превръща в 72-минутен, без съобщение за грешка.
    """
    d = (df.sort_values(["entity_id", "time_index"])
           .groupby("entity_id")["time_index"].diff()
           .dt.total_seconds().div(60).dropna())
    if d.empty:
        return 10.0
    return float(round(d.median(), 2))


def build_features(df, horizon_h, step_min):
    """Постъпкови признаци, изчислими и в потоков режим (вж. ch3_09)."""
    out = []
    h = max(1, int(horizon_h * 60 / step_min))

    for zid, g in df.groupby("entity_id"):
        g = g.sort_values("time_index").reset_index(drop=True).copy()
        m = pd.to_numeric(g["soilmoisture"], errors="coerce")

        g["m"] = m
        g["m_d1"] = m.diff()
        g["m_d6"] = m.diff(6)
        g["m_mean_1h"] = m.rolling(max(2, int(60 / step_min)), min_periods=1).mean()
        g["m_std_3h"] = m.rolling(max(3, int(180 / step_min)), min_periods=2).std()
        g["m_min_6h"] = m.rolling(max(3, int(360 / step_min)), min_periods=1).min()
        g["at"] = pd.to_numeric(g["airtemperature"], errors="coerce")
        g["ah"] = pd.to_numeric(g["airhumidity"], errors="coerce")
        g["at_mean_3h"] = g["at"].rolling(max(3, int(180 / step_min)), min_periods=1).mean()
        g["vpd"] = g["at"] * (1 - g["ah"] / 100.0)
        g["hour"] = g["time_index"].dt.hour + g["time_index"].dt.minute / 60
        g["hour_sin"] = np.sin(2 * np.pi * g["hour"] / 24)
        g["hour_cos"] = np.cos(2 * np.pi * g["hour"] / 24)
        g["mmin"] = pd.to_numeric(g["moisturemin"], errors="coerce")
        g["margin"] = g["m"] - g["mmin"]
        g["irr"] = pd.to_numeric(g.get("irrigationstate", 0), errors="coerce").fillna(0)

        # Две цели. Класификацията отговаря на въпроса, който системата
        # действително решава, но се изражда, ако прагът не е пресичан през
        # периода. Регресията е добре поставена и в двата случая.
        fut_min = m.shift(-1).rolling(h, min_periods=1).min().shift(-(h - 1))
        g["y_clf"] = (fut_min < g["mmin"]).astype("float")
        g.loc[fut_min.isna(), "y_clf"] = np.nan
        g["y_reg"] = m.shift(-h)          # влажност след H часа
        out.append(g)

    d = pd.concat(out, ignore_index=True)
    return d.dropna(subset=FEATURES)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--tag", default="run")
    p.add_argument("--horizon", type=float, default=6.0, help="часове напред")
    p.add_argument("--step", type=float, default=0,
                   help="минути между записите; 0 = автоматично от данните")
    p.add_argument("--task", choices=["clf", "reg", "auto"], default="auto",
                   help="auto = регресия, ако класовете са изродени")
    p.add_argument("--split", type=float, default=0.7)
    a = p.parse_args()

    import lightgbm as lgb
    from sklearn.metrics import (accuracy_score, average_precision_score,
                                 f1_score, roc_auc_score, confusion_matrix,
                                 mean_absolute_error, mean_squared_error, r2_score)

    raw = pd.read_csv(a.csv)
    raw["time_index"] = pd.to_datetime(raw["time_index"], format="ISO8601", utc=True)
    raw = raw.dropna(subset=["time_index"])
    step = a.step if a.step > 0 else detect_step(raw)
    print(f"[интервал] {step} min между записите"
          f"{' (открит автоматично)' if a.step == 0 else ' (зададен)'}")
    print(f"[хоризонт] {a.horizon} h = {int(a.horizon * 60 / step)} записа напред")
    df = build_features(raw, a.horizon, step).sort_values("time_index")

    # избор на задача
    task = a.task
    pos = df["y_clf"].dropna()
    rate = pos.mean() if len(pos) else 0.0
    print(f"[класове] дял на положителните: {rate:.4f} ({int(pos.sum())} от {len(pos)})")
    if task == "auto":
        task = "clf" if 0.02 <= rate <= 0.98 else "reg"
        if task == "reg":
            print("[задача] класовете са изродени -> преминаване към РЕГРЕСИЯ")
            print("         (влажността почти не е пресичала прага през периода)")
    print(f"[задача] {'класификация' if task == 'clf' else 'регресия'}")

    ycol = "y_clf" if task == "clf" else "y_reg"
    df = df.dropna(subset=[ycol])
    cut = df["time_index"].quantile(a.split)
    tr, te = df[df["time_index"] <= cut], df[df["time_index"] > cut]
    print(f"[разделяне по време] граница {cut}")
    print(f"[обучение] {len(tr)} реда   [проверка] {len(te)} реда")
    if len(tr) < 50 or len(te) < 50:
        raise SystemExit("Твърде малко редове след изчистването. Намалете --horizon.")

    common = dict(n_estimators=400, learning_rate=0.05, num_leaves=31,
                  min_child_samples=40, subsample=0.9, colsample_bytree=0.9,
                  random_state=42, verbose=-1)
    res = {"набор": a.tag, "задача": task, "хоризонт_h": a.horizon,
           "интервал_min": step, "n_обучение": len(tr), "n_проверка": len(te)}

    if task == "clf":
        model = lgb.LGBMClassifier(**common)
        model.fit(tr[FEATURES], tr[ycol])
        prob = model.predict_proba(te[FEATURES])[:, 1]
        pred = (prob >= 0.5).astype(int)
        tn, fp, fn, tp = confusion_matrix(te[ycol], pred, labels=[0, 1]).ravel()
        res.update({
            "accuracy": round(accuracy_score(te[ycol], pred), 4),
            "f1": round(f1_score(te[ycol], pred, zero_division=0), 4),
            "roc_auc": round(roc_auc_score(te[ycol], prob), 4) if te[ycol].nunique() > 1 else None,
            "pr_auc": round(average_precision_score(te[ycol], prob), 4) if te[ycol].nunique() > 1 else None,
            "TN": int(tn), "FP": int(fp), "FN": int(fn), "TP": int(tp),
        })
    else:
        model = lgb.LGBMRegressor(**common)
        model.fit(tr[FEATURES], tr[ycol])
        pred = model.predict(te[FEATURES])
        # отправна база: влажността не се променя за H часа
        naive = te["m"].values
        res.update({
            "MAE_пункта": round(mean_absolute_error(te[ycol], pred), 3),
            "RMSE_пункта": round(mean_squared_error(te[ycol], pred) ** 0.5, 3),
            "R2": round(r2_score(te[ycol], pred), 4),
            "MAE_база_без_промяна": round(mean_absolute_error(te[ycol], naive), 3),
            "подобрение_спрямо_базата_%": round(
                100 * (1 - mean_absolute_error(te[ycol], pred)
                       / max(1e-9, mean_absolute_error(te[ycol], naive))), 1),
        })

    print("\n=== Пакетен модел (LightGBM) ===")
    for k, v in res.items():
        print(f"  {k:12s} {v}")

    imp = (pd.Series(model.feature_importances_, index=FEATURES)
           .sort_values(ascending=False))
    print("\n[принос на признаците]")
    print(imp.head(10).to_string())

    with open(f"ml_batch_{a.tag}.json", "w") as fh:
        json.dump({"metrics": res, "importance": imp.to_dict()}, fh,
                  ensure_ascii=False, indent=2)
    imp.to_csv(f"ml_batch_{a.tag}_importance.csv")
    print(f"\n[out] ml_batch_{a.tag}.json")
    print("\nБЕЛЕЖКА: показателите от синтетични и от реални данни се представят")
    print("в ОТДЕЛНИ таблици в тезата — не се смесват.")


if __name__ == "__main__":
    main()
