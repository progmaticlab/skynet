#!/bin/bash

DIR=${1:-"."}
STASH=intermediate

while true
do
	d=$(date -Iseconds)
	kubectl describe pods | grep -w "Name:\|Node:" > "${DIR}/pods.$d"
	for pod in `kubectl get pods --no-headers -o custom-columns=":metadata.name" --field-selector=status.phase=Running` 
	do 
		#kubectl exec -it $pod  -c istio-proxy  -- sh -c 'curl localhost:15000/stats' | gzip > $pod.$d.gz
		echo "Storing data for pod $pod.$d"
		kubectl exec $pod  -c istio-proxy  -- sh -c 'curl localhost:15000/stats | grep 9080' > ${DIR}/$STASH
		if [[ 0 -eq $? ]]
		then
			mv ${DIR}/$STASH ${DIR}/$pod.$d
		fi	
	done
done
