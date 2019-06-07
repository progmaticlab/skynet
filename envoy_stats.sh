#!/bin/bash

while true
do
	d=`date -Iseconds`
	for pod in `kubectl get pods --no-headers -o custom-columns=":metadata.name"`
	do 
		#kubectl exec -it $pod  -c istio-proxy  -- sh -c 'curl localhost:15000/stats' | gzip > $pod.$d.gz
		kubectl exec -it $pod  -c istio-proxy  -- sh -c 'curl localhost:15000/stats' > $pod.$d
	done
	kubectl describe pods | grep -w "Name:\|Node:" > "pods.$d"
	sleep 5
done
