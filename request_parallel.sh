#!/bin/bash

# miliseconds
MAX_PAUSE=${1:-${MAX_PAUSE:-100}}
THREADS=${2:-${THREADS:-10}}
URL=${URL:-"http://$GATEWAY_URL/productpage"}

STAT_DIR=${STAT_DIR:-"./requests_stat"}

declare -A jobs

if [[ -z "$GATEWAY_URL" ]] ; then
  echo "ERROR: GATEWAY_URL variable must point be provided"
  exit -1
fi

function do_request_sleep() {
  local myjob=$1
  local requests=0
  local errors=0
  if [[ -n "$STAT_DIR" && -e $STAT_DIR ]] ; then
    rm -f ${STAT_DIR}/${myjob}.stat
  fi
  while true ; do 
    local v=${RANDOM}
    local ms_max=${MAX_PAUSE}
    local ss=0
    if (( ${MAX_PAUSE} >= 1000 )) ; then
      ss=$(( v % ${MAX_PAUSE} ))
      ss=$(( ss / 1000 ))
      ms_max=$(( MAX_PAUSE % 1000 ))
    fi
    local ms=0
    if (( ${MAX_PAUSE} > 0 )) ; then 
      ms=$(( v % 1000 ))
      ms=$(( ms % ms_max ))
    fi
    sleep ${ss}.${ms}
    local start=$(( $( date +"%s%N") / 1000 ))
    curl "$URL" > /dev/null 2>&1 || (( errors += 1 ))
    local end=$(( $( date +"%s%N") / 1000 ))
    local dur=$(( end - start ))
    (( requests += 1 ))
    if [[ -n "$STAT_DIR" && -e $STAT_DIR ]] ; then
      echo -e "$myjob\t$requests\t$errors\t$dur" >> ${STAT_DIR}/${myjob}.stat
    fi
  done
}

function cleanup_jobs() {
  local j
  echo "Cancel.."
  for j in ${jobs[@]} ; do 
    echo "Kill job $j" 
    kill $j
    wait $j >/dev/null 2>&1
  done
}

trap cleanup_jobs INT

[ -n "$STAT_DIR" ] && mkdir -p $STAT_DIR

echo "Start $THREADS CURL workers..."
count=$THREADS
while (( count > 0 )) ; do
  do_request_sleep $count &
  jobs[$count]=$!
  echo "Started jobs ${jobs[$count]}=$count"
  (( count -= 1 ))
done

jobs

echo "Wait for cancel"
wait

echo "End"
