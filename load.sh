#!/bin/bash
echo "load $1" $(date +"%T.%N") > /host/load_time.log
case $1 in
    'cpu')
        /host/load_cpu_mem.sh
        ;;
    'net')
        /host/load_net.sh 
        ;;
    'disk')
        /host/load_disk.sh
        ;;
esac
echo "load $1" $(date +"%T.%N") >> /host/load_time.log
