#!/usr/bin/env bash
# ============================================================================
# ch3_05_sweep.sh — пълният експеримент за мащабируемост (раздел 3.6)
#
# Развъртане 3 -> 10 -> 25 -> 50 -> 100 зони, по 10 минути всяко, плюс един
# пробег без разсейване във времето (най-неблагоприятният случай).
#
# Общо време: около час и десет минути. Пуска се на сървъра или на машина
# в същата мрежа. НЕ пускайте, докато трите реални зони поливат.
#
#   chmod +x ch3_05_sweep.sh
#   ./ch3_05_sweep.sh
# ============================================================================
set -euo pipefail

DUR=${DUR:-600}          # секунди на пробег
INTERVAL=${INTERVAL:-10} # секунди между измерванията на зона
STEPS=${STEPS:-"3 10 25 50 100"}
MAXN=100                 # регистрираме веднъж максимума и го използваме за всички

echo "=== регистрация на $MAXN синтетични зони ==="
python3 ch3_03_provision.py --count $MAXN

echo
for N in $STEPS; do
  echo "=== пробег N=$N, разсеян във времето, ${DUR}s ==="
  python3 ch3_04_load.py --count "$N" --duration "$DUR" --interval "$INTERVAL" \
      --jitter --clients "$N" --probe-every 5 --out-prefix jitter
  echo "   пауза 60 s за успокояване на опашките"
  sleep 60
done

echo "=== контролен пробег без разсейване, N=50 (най-неблагоприятен случай) ==="
python3 ch3_04_load.py --count 50 --duration "$DUR" --interval "$INTERVAL" \
    --clients 50 --probe-every 5 --out-prefix lockstep
sleep 60

echo
echo "=== почистване ==="
read -r -p "Да се премахнат ли синтетичните устройства и ентитети? [y/N] " ans
if [[ "$ans" == "y" ]]; then
  python3 ch3_03_provision.py --count $MAXN --delete
  for i in $(seq -w 1 $MAXN); do
    curl -sX DELETE "http://localhost:1026/v2/entities/SynthZone:$i" \
      -H "fiware-service: smartfarm" -H "fiware-servicepath: /" >/dev/null || true
  done
  curl -s -X POST "http://localhost:4200/_sql" -H 'Content-Type: application/json' \
    -d '{"stmt":"DROP TABLE IF EXISTS \"mtsmartfarm\".\"etsynthzone\""}' >/dev/null
  echo "   премахнати"
else
  echo "   пропуснато — премахнете ръчно преди защитата"
fi

echo
echo "Готово. Следва: python3 ch3_06_sweep_report.py"
