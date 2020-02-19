#!/bin/bash

# $1 - Time in seconds or number of curls if sleep is 0
# $2 - Sleep time
function request() {
        if [ $2 -ne 0 ]
        then 
                n=$(echo $1 / $2 | bc)
        else
                n=$1
        fi
        for ((i=0; i<n; i++))
        do
                curl "http://$GATEWAY_URL/productpage"
                echo "Curling: ", $1, $2, $i
                sleep $2
        done
}

while true
do
        request 3 0.2
        request 30 0
#        request 30 0.2
#        request 30 0.01
#        request 30 0.1
#        request 30 0.01
done
q
