#!/bin/bash
# dataset_v4(센서 1.5m)만: 시드마다 변형을 하나씩 돌린다. 결과가 있으면 건너뛴다.
E=$(dirname "$0"); cd "$E"
export PYTHONPATH=${PYTHONPATH:-$E/../pylib}   # Pi는 xgboost·lightgbm을 별도 경로에 둠
PY=${PY:-/home/pi/Documents/Github/Majubom/.venv/bin/python}
run() { local d=$1 v=$2 s=$3; local o=res/$d/${v}_$s.json
  [ -f "$o" ] && return
  nice -n 10 $PY run_roi.py $d $v $s $o 2>&1 | grep -v -i warning >> queue.log; }
for s in 42 0 1 2 3; do
  for v in none roi_py base_valid base_off act2 act5 cols; do run v4 $v $s; done
done
echo ALLDONE >> queue.log
