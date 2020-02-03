#!/bin/bash

host = $(hostname)
if [[ $host == 'node3']]; then
    $host = 'node2'
else
    $host = 'node3'
fi
for (( i=1; i<1000; i++)); do
    ping -s 65507 $host
done
