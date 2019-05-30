#!/bin/bash

while true
do
        for pod in `kubectl get pods --no-headers -o custom-columns=":metadata.name"`
        do 
                d=`date -Iseconds`
                #kubectl exec -it $pod  -c istio-proxy  -- sh -c 'curl localhost:15000/stats' | gzip > $pod.$d.gz
                kubectl exec -it $pod  -c istio-proxy  -- sh -c 'curl localhost:15000/stats' > $pod.$d
        done
        sleep 5
done