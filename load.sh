#!/bin/bash

# $1 - Time in seconds
# $2 - Sleep time
function request() {
        n=$(echo $1 / $2 | bc)
        for ((i=0; i<n; i++))
        do
                curl "http://10.50.10.185:31380/productpage"
                echo "Curling: ", $1, $2, $i
                sleep $2
        done
}

while true
do
        request 30 1
        request 30 0.1
        request 30 0.5
        request 30 0.2
done
