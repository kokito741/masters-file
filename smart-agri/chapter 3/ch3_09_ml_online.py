#!/usr/bin/env python3
"""
ch3_09_ml_online.py — онлайн обучение, дрейф и аномалии (раздели 3.5.3, 3.5.4).

Три отделни оценки върху един и същ поток:
  1. ARF с последователно оценяване — точността във времето
  2. ADWIN върху грешката — откриване на дрейф, съпоставено с известния момент
  3. HalfSpaceTrees — откриване на аномалии, съпоставено с известните етикети

Последователното оценяване означава: всяко наблюдение първо се използва за
предсказване, после за обучение. Времевата подредба не се нарушава.

    pip install river pandas numpy matplotlib --break-system-packages
    python3 ch3_09_ml_online.py --csv synth.csv --tag synth
    python3 ch3_09_ml_online.py --csv zones.csv --tag real --no-truth
"""
import argparse
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ch3_08_ml_batch import build_features, detect_step, FEATURES  # общи признаци

# River мести класовете между редакциите — поемаме и двата варианта
try:
    from river.forest import ARFClassifier, ARFRegressor
except ImportError:
    from river.ensemble import AdaptiveRandomForestClassifier as ARFClassifier
    from river.ensemble import AdaptiveRandomForestRegressor as ARFRegressor
from river import anomaly, drift, metrics, preprocessing


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--tag", default="run")
    p.add_argument("--horizon", type=float, default=6.0)
    p.add_argument("--step", type=float, default=0,
                   help="0 = автоматично от данните")
    p.add_argument("--task", choices=["clf", "reg", "auto"], default="auto")
    p.add_argument("--warmup", type=int, default=200,
                   help="наблюдения за загряване: не влизат в показателите и в ADWIN")
    p.add_argument("--adwin-delta", type=float, default=0.002,
                   help="доверие на ADWIN: по-малка стойност = по-малко "
                        "установявания. Отчитайте няколко стойности, не една.")
    p.add_argument("--err-cap", type=float, default=20.0,
                   help="ограничение на абсолютната грешка при регресия")
    p.add_argument("--window", type=int, default=500, help="прозорец за пълзящата точност")
    p.add_argument("--no-truth", action="store_true",
                   help="реални данни: няма известен дрейф и етикети за аномалии")
    a = p.parse_args()

    raw = pd.read_csv(a.csv)
    raw["time_index"] = pd.to_datetime(raw["time_index"], format="ISO8601", utc=True)
    raw = raw.dropna(subset=["time_index"])
    step = a.step if a.step > 0 else detect_step(raw)
    print(f"[интервал] {step} min между записите")
    df = build_features(raw, a.horizon, step).sort_values("time_index").reset_index(drop=True)

    # Етикетите за аномалии се свързват ПО КЛЮЧ, а не по позиция: изчистването
    # на редове в build_features разминава индексите и всяко сравнение по
    # позиция би дало безсмислени показатели.
    if "is_anomaly" in raw.columns:
        df = df.merge(raw[["entity_id", "time_index", "is_anomaly"]],
                      on=["entity_id", "time_index"], how="left",
                      suffixes=("", "_truth"))
        df["is_anomaly"] = df["is_anomaly"].fillna(0).astype(int)
    feats = FEATURES

    pos = df["y_clf"].dropna()
    rate = pos.mean() if len(pos) else 0.0
    task = a.task
    if task == "auto":
        task = "clf" if 0.02 <= rate <= 0.98 else "reg"
    ycol = "y_clf" if task == "clf" else "y_reg"
    df = df.dropna(subset=[ycol]).reset_index(drop=True)
    print(f"[поток] {len(df)} наблюдения, {len(feats)} признака")
    print(f"[задача] {'класификация' if task == 'clf' else 'регресия'} "
          f"(дял положителни {rate:.4f})")

    truth_drift = None
    res_delay_h = None
    if not a.no_truth and "drift_point" in raw.columns:
        # позицията на дрейфа в подредения по време поток
        dp = int(raw["drift_point"].iloc[0])
        t_drift = raw.sort_values("time_index")["time_index"].iloc[
            min(dp * raw["entity_id"].nunique(), len(raw) - 1)]
        truth_drift = int((df["time_index"] <= t_drift).sum())
        print(f"[истина] дрейфът започва около наблюдение {truth_drift}")

    # ---- 1 и 2: модел + откриване на дрейф ----
    # ADWIN се захранва с ГРЕШКАТА на модела, не със самите данни: интересува
    # ни изменение в отношението между признаци и цел, а не в разпределението
    # на отделен признак.
    if task == "clf":
        model, m1, m2 = ARFClassifier(n_models=10, seed=42), metrics.Accuracy(), metrics.F1()
    else:
        model, m1, m2 = ARFRegressor(n_models=10, seed=42), metrics.MAE(), metrics.RMSE()
    adwin = drift.ADWIN(delta=a.adwin_delta)

    rolling, drift_points, win = [], [], []

    for i, row in enumerate(df.itertuples(index=False)):
        x = {f: getattr(row, f) for f in feats}
        y = int(getattr(row, ycol)) if task == "clf" else float(getattr(row, ycol))

        y_hat = model.predict_one(x)
        if y_hat is not None and i >= a.warmup:
            # Загряването се изключва: при студен старт ансамбълът дава
            # произволни стойности, чиято дисперсия заглушава ADWIN и
            # изкривява показателите.
            m1.update(y, y_hat)
            m2.update(y, y_hat)
            if task == "clf":
                err = int(y_hat != y)
            else:
                # Ограничаване на грешката: единични отклонения от десетки
                # пункта иначе доминират дисперсията и правят детектора
                # нечувствителен към трайното изменение, което търсим.
                err = min(abs(y_hat - y), a.err_cap)
            win.append(err)
            if len(win) > a.window:
                win.pop(0)
            rolling.append((1 - np.mean(win)) if task == "clf" else np.mean(win))
            adwin.update(err)
            if adwin.drift_detected:
                drift_points.append(i)
        else:
            rolling.append(np.nan)

        model.learn_one(x, y)

    print("\n=== Онлайн модел (ARF, последователна оценка) ===")
    print(f"  Загряване: първите {a.warmup} наблюдения не участват")
    print(f"  {'Accuracy' if task == 'clf' else 'MAE '} : {m1.get():.4f}")
    print(f"  {'F1      ' if task == 'clf' else 'RMSE'} : {m2.get():.4f}")
    print(f"  Открити изменения от ADWIN: {len(drift_points)}")
    if drift_points:
        print(f"  Позиции: {drift_points[:12]}{' ...' if len(drift_points) > 12 else ''}")

    detect_delay = None
    if truth_drift is not None:
        after = [d for d in drift_points if d >= truth_drift]
        before = [d for d in drift_points if d < truth_drift]
        if after:
            detect_delay = after[0] - truth_drift
            # закъснението в реално време се чете от времевите белези, не се
            # изчислява от броя наблюдения: при няколко зони потокът е преплетен
            t_a = df["time_index"].iloc[min(after[0], len(df) - 1)]
            t_b = df["time_index"].iloc[min(truth_drift, len(df) - 1)]
            delay_h = (t_a - t_b).total_seconds() / 3600
            print(f"  Закъснение на откриване: {detect_delay} наблюдения "
                  f"({delay_h:.1f} h)")
            res_delay_h = round(delay_h, 1)
        else:
            res_delay_h = None
            print("  Дрейфът НЕ беше открит след истинския момент")
        print(f"  Установявания преди истинския момент (погрешни): {len(before)}")

    # ---- 3: аномалии ----
    # Детекторът НЕ получава същите признаци като класификатора. Пиковете и
    # залепванията се проявяват в суровата стойност и в първата ѝ разлика;
    # изгладените признаци (пълзящи средни) ги разтварят и ги правят невидими.
    HST_FEATS = ["m", "m_d1", "abs_d1", "m_std_3h"]
    df["abs_d1"] = df["m_d1"].abs()

    hst = anomaly.HalfSpaceTrees(n_trees=25, height=8, window_size=250, seed=42)
    scaler = preprocessing.MinMaxScaler()
    scores = []
    for row in df.itertuples(index=False):
        x = {f: getattr(row, f) for f in HST_FEATS}
        scaler.learn_one(x)
        x = scaler.transform_one(x)
        scores.append(hst.score_one(x))
        hst.learn_one(x)
    scores = np.array(scores)

    anom = {"n": len(scores), "среден_резултат": round(float(scores.mean()), 4)}
    if not a.no_truth and "is_anomaly" in df.columns:
        lbl = df["is_anomaly"].values
        # прагът се задава като дела на действителните аномалии, а не произволно
        rate = max(lbl.mean(), 0.001)
        thr = np.quantile(scores, 1 - rate)
        pred = (scores >= thr).astype(int)
        tp = int(((pred == 1) & (lbl == 1)).sum())
        fp = int(((pred == 1) & (lbl == 0)).sum())
        fn = int(((pred == 0) & (lbl == 1)).sum())
        prec = tp / max(1, tp + fp)
        rec = tp / max(1, tp + fn)
        anom.update({
            "дял_аномалии": round(float(rate), 4),
            "праг": round(float(thr), 4), "TP": tp, "FP": fp, "FN": fn,
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(2 * prec * rec / max(1e-9, prec + rec), 4),
        })
    # Честна бележка за обхвата: отпаданията (липсваща стойност) се изхвърлят
    # още при изграждането на признаците и следователно НЕ МОГАТ да бъдат
    # открити от този детектор. Те подлежат на откриване от проверката за
    # давност на данните, описана в подраздел 2.3.8.
    if "is_anomaly" in raw.columns:
        total_anom = int(raw["is_anomaly"].sum())
        kept_anom = int(df["is_anomaly"].sum()) if "is_anomaly" in df.columns else 0
        anom["внесени_общо"] = total_anom
        anom["достигнали_детектора"] = kept_anom
        anom["изхвърлени_като_липсващи"] = total_anom - kept_anom

    print("\n=== Откриване на аномалии (HalfSpaceTrees) ===")
    for k, v in anom.items():
        print(f"  {k:16s} {v}")

    # ---- изход ----
    res = {
        "набор": a.tag,
        "n": len(df),
        "загряване": a.warmup,
        "задача": task,
        "метрика_1": round(m1.get(), 4),
        "метрика_2": round(m2.get(), 4),
        "adwin_delta": a.adwin_delta,
        "adwin_установявания": len(drift_points),
        "adwin_позиции": drift_points[:50],
        "истински_дрейф": truth_drift,
        "закъснение_наблюдения": detect_delay,
        "закъснение_часове": res_delay_h,
        "аномалии": anom,
    }
    with open(f"ml_online_{a.tag}.json", "w") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)

    # фиг. — точност във времето с отбелязани установявания
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(rolling, lw=1.0, label=f"пълзяща {'точност' if task == 'clf' else 'грешка'} (прозорец {a.window})")
    for d in drift_points:
        ax.axvline(d, color="grey", ls=":", lw=.8)
    if truth_drift is not None:
        ax.axvline(truth_drift, color="red", ls="--", lw=1.6, label="внесен дрейф")
    ax.set_xlabel("наблюдение")
    ax.set_ylabel("точност" if task == "clf" else "MAE, пункта")
    if task == "clf":
        ax.set_ylim(0, 1.02)
    ax.grid(alpha=.3)
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(f"fig_3_7_online_{a.tag}.png", dpi=160)
    plt.close(fig)

    print(f"\n[out] ml_online_{a.tag}.json  fig_3_7_online_{a.tag}.png")


if __name__ == "__main__":
    main()
