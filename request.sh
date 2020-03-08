#!/bin/bash

# $1 - Number of curls
# $2 - Sleep time
function request() {
        local n=$1
        for ((i=0; i<n; i++))
        do
                local error=0
                local response=
                if ! response=$(curl -s --fail -w "\ntime_total: %{time_total}\n" "http://$GATEWAY_URL/productpage") ; then
                        error=1
                fi
                local tt=$(echo "$response" | awk '/time_total: / {print($2)}')
                echo "Curling: max=$1, pause=$2, count=$i, rq_time: $tt error: $error"
                sleep $2
        done
}

function load() {
        local count=$1
        for ((i=0; i<count; i++)) ; do
                request 3 0.2
                request 30 0
        #       request 30 0.2
        #       request 30 0.01
        #       request 30 0.1
        #       request 30 0.01
        done
}

count=${1:-65535}
load $count | awk '/Curling:.*rq_time:/{sum+=$6; errs+=$8} END {if (NR > 0) {print("send_requests: "NR"   time_average: "sum/NR"    errors: "errs)}}'
