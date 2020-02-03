#!/bin/bash

case $1 in
    'cpu')
        ./stress_cpu_mem.sh
        ;;
#    'net')
#        ./stress_net.sh
#        ;;
#    'disk')
#        ./stress_disk.sh
#        ;;
esac
