#!/bin/bash

case $1 in
    'cpu')
        /host/stress_cpu_mem.sh
        ;;
    'net')
        /host/stress_net.sh 
        ;;
    'disk')
        ./stress_disk.sh
        ;;
esac