#!/bin/bash

for (( i=0; i<2; i++ )); do
    dd iflag=direct if=/host/disk_load.data of=/dev/null
done
