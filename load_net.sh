#!/bin/bash

host = $(hostname)
if [[ $host == 'node3' ]]; then
    host = 'node2'
else
    host = 'node3'
    
for (( i=1; i<1000; i++))
ping -s 65507 -c 1 $host > /dev/null
