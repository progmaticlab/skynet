#!/bin/bash

DIR=${1:-"."}
THIS=$$
STASH=intermediate
RECORDINGS=${DIR}/../recordings

if [[ -d ${RECORDINGS} ]]
then
        ls -tr ${RECORDINGS}/pods.* | (
                shopt -s extglob
                while read x
                do
			d=${x/#.*[.]/}
			p=($(grep 'Name:' "$x" | while read X; do y=($X); [[ ${y[1]} == httpbin-* ]] || echo ${y[1]}; done))
			for n in ${p[@]}
			do
				i=${RECORDINGS}/$n.$d
				[[ -e $i ]] || break
			done
			[[ -e $i ]] || continue

			w=$(date -Iseconds)
			kubectl describe pods | grep -w "Name:\|Node:" > "${DIR}/pods.$w"

			for pod in `kubectl get pods --no-headers -o custom-columns=":metadata.name" --field-selector=status.phase=Running` 
			do
				[[ $pod == httpbin-* ]] && continue
				for n in ${p[@]}
				do
					P=${n/%-+([^-])-+([^-])/}
					[[ $pod == ${P}-* ]] || continue
					cp ${RECORDINGS}/${n}.${d} ${DIR}/${STASH}
					if [[ 0 -eq $? ]]
					then
						mv ${DIR}/$STASH ${DIR}/$pod.$w
					fi
				done
			done
			kill -s 0 $THIS || exit
			sleep 3
                done
                shopt -u extglob
        )
fi
while true
do
	d=$(date -Iseconds)
	kubectl describe pods | grep -w "^Name:\|^Node:" > "${DIR}/pods.$d"
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
