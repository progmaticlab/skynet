#!/bin/bash

NC='\e[0m'
BOX=$(pwd)/.sandbox/
RED='\e[95m'
SKYNET=${BOX}/skynet
BOLD='\e[1m'
CYAN='\e[96m'
PATH=$PATH:$BOX
GREEN='\e[92m'
HEALER_MUTEX=$BOX/.healer
SSH_PUBLIC_KEY=$(find ~/.ssh/id_*.pub | head -n 1)
MONITOR_TRANSPORT=$BOX/.transport

function rip() {
	eval "local x=\$$1"
	if [[ 0 -lt $x ]]
	then
		kill -KILL $x
		wait $x 2>/dev/null
		eval "$1=0"
	fi
}

function make_uuid() {
	python -c "import uuid; print(uuid.uuid1())"
}

B=(no yes)

################################################################################
## Collecting block

C=0
CC=0
MC=(collect 'stop collecting')
function stop_collecting() {
	rip "CC"
}

function toggle_collecting() {
	if [[ 1 -eq $C ]]
	then
		pushd ${BOX}/data >/dev/null
		${SKYNET}/envoy_stats.sh 2>/dev/null 1>&2 &
		CC=$!
		popd > /dev/null
	else
		stop_collecting
	fi
}

function show_collecting_status() {
	printf "\033[s\033[5;2H${CYAN}Collecting${NC}: ${B[$C]}  \033[u"
}

################################################################################
## Loading block

L=0
LC=0
ML=(load 'stop loading')
function stop_loading() {
	rip "LC"
}

function toggle_loading() {
	if [[ 1 -eq $L ]]
	then
		GATEWAY_URL="$HOST_PORT" bash "${SKYNET}/request.sh" 2>/dev/null 1>&2 &
		LC=$!
	else
		rip "LC"
	fi
}

function show_loading_status() {
	printf "\033[s\033[4;2H${CYAN}Loading${NC}: ${B[$L]}  \033[u"
}

################################################################################
## Stress v# block

Sv1=0
MSv1=('stress reviews-v1' 'stop stressing reviews-v1')
function show_stressing_v1_status() {
	printf "\033[s\033[6;2H${CYAN}Stressing reviews-v1${NC}: ${B[$Sv1]}  \033[u"
}

Sv2=0
MSv2=('stress reviews-v2' 'stop stressing reviews-v2')
function show_stressing_v2_status() {
	printf "\033[s\033[7;2H${CYAN}Stressing reviews-v2${NC}: ${B[$Sv2]}  \033[u"
}

function push_load_sh() {
	cat <<INSTRUCTIONS | ssh -o StrictHostKeyChecking=no -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null -i ${SSH_PUBLIC_KEY/%[.]pub/} ec2-user@$POD_CARRIER sudo -- bash - 2>/dev/null
cat <<LOADSH > /host/load.sh
#!/bin/bash
Sv1=${Sv1}
Sv2=${Sv2}
LOADSH
cat <<'LOADSH' >> /host/load.sh
s=\${SERVERDIRNAME}-\${SERVICE_VERSION}
echo "\$s: load \$1" \$(date +"%T.%N") >> /host/load_time.log
if [[ 1 -eq \${Sv1} ]] && [[ "\${SERVICE_VERSION}" == 'v1' ]]
then
	echo "\$s: SLEEP" \$(date +"%T.%N") >> /host/load_time.log
	sleep 1
fi
if [[ 1 -eq \${Sv2} ]] && [[ "\${SERVICE_VERSION}" == 'v2' ]]
then
	echo "\$s: SLEEP" \$(date +"%T.%N") >> /host/load_time.log
	sleep 1
fi
echo "\$s: load \$1" \$(date +"%T.%N") >> /host/load_time.log
LOADSH
INSTRUCTIONS
}

################################################################################
## Training block

T=0
MT=('train' 'stop training')
function show_training_status() {
	printf "\033[s\033[8;2H${CYAN}Learning${NC}: ${B[$T]}  \033[u"
}

pushd $BOX > /dev/null

################################################################################
## Monitor block

MX=0
MY=0
function query_anomalies() {
	if [[ 0 -eq ${MX} ]]; then return -1; fi

	local f=$BOX/$(make_uuid)
	mkfifo $f

	local h
	exec {h}>${MONITOR_TRANSPORT}
	flock -x ${h}
	printf "{\"command\": \"list_anomalies\", \"promise\": \"${f}\"}\0" >&${MONITOR_CHANNEL}

	local j=$(< $f)
	rm -f $f

	printf '%s' $j
}

function pull_anomalies() {
	local j=$(query_anomalies)
	if [[ 0 -ne $? ]]; then return 0; fi

	local a=($(printf '%s' $j | jq '.[].name' | tr -d '"'))
	local b=($(printf '%s' $j | jq '.[].count' | tr -d '"'))

	for((i=0;i<8;++i))
	do
		local k=$((i + 4))
		printf "\033[s\033[${k};42H\033[K\033[u";
	done
	for i in ${!a[*]}
	do
		local k=$((i + 4))
		printf "\033[s\033[${k};42H${RED}${a[$i]}${NC}: ${b[$i]}\033[u"
	done
}

function pull_learning_status() {
	if [[ 0 -lt ${MX} ]]
	then
		local f=$BOX/$(make_uuid)
		mkfifo $f
		flock -x ${MONITOR_CHANNEL}
		printf "{\"command\": \"is_learning\", \"promise\": \"${f}\"}\0" >&${MONITOR_CHANNEL}
		flock -u ${MONITOR_CHANNEL}

		local j=$(< $f)
		rm -f $f
		local a=($(printf '%s' $j | jq '.learning'))
		case "$a" in
			true) T=1;;
			*) T=0;;
		esac
	fi
}

function toggle_learning() {
	if [[ 0 -lt ${MX} ]]
	then
		flock -x ${MONITOR_CHANNEL}
		printf "{\"command\": \"toggle_learning\"}\0" >&${MONITOR_CHANNEL}
		flock -u ${MONITOR_CHANNEL}
	fi
}

function track_anomalies() {
	while true
	do
		pull_anomalies
		sleep 3
	done
}

function start_monitor() {
	rm -f ${MONITOR_TRANSPORT}
	mkfifo ${MONITOR_TRANSPORT}
	${SKYNET}/monitor_envoy_stats.py ./data -r ./ref/refstats -B -p product details ratings reviews-v1 reviews-v2 2> /dev/null 1>&2 < ${MONITOR_TRANSPORT} &
	MX=$!
	exec {MONITOR_CHANNEL}> ${MONITOR_TRANSPORT}
	track_anomalies &
	MY=$!
}

function stop_monitor() {
	if [[ 0 -lt $MX ]]
	then
		rip "MY"
		echo -ne "{"command": "quit"}\0" >&${MONITOR_CHANNEL}
		wait $MX 2>/dev/null
		MX=0
	fi
}

################################################################################
## Healing block

function reset_anomaly() {
	echo ${BOX}/kubectl delete pod $1 >> ${BOX}/kube.log
	${BOX}/kubectl delete pod $1 2>> ${BOX}/kube.log 1>&2
	echo 1 >& $2
}

function deploy_healer() {
	touch ${HEALER_MUTEX}
}

function reset_anomalies_() {
	local g
	exec {g}>${HEALER_MUTEX}
	flock -x ${g}

	local j=$(query_anomalies)
	if [[ 0 -ne $? ]]; then return 0; fi

	local a=($(printf '%s' $j | jq '.[].name' | tr -d '"'))
	if [[ 0 -eq ${#a[@]} ]]; then return 0; fi

	local z=(/ '\u2014' \\ \| / '\u2014' \\ \|)
	for p in ${a[@]}
	do
		local f=$BOX/$(make_uuid)
		mkfifo $f
		exec {d}<> $f
		reset_anomaly $p $d &
		local c=$!

		local j=0
		while ! read -t 0 -u $d
		do
			local k=$((j % ${#z[@]}))
			printf '\033[s\033[3;51H\033[K\033[3;51Hhealing %b\033[u' ${z[k]};
			sleep 2
			j=$((j + 1))
		done
		exec {d}<&-
		wait $c 2>/dev/null
		rm -f $f
	done
	printf "\033[s\033[3;50H\033[K\033[u";
}

HC=0
function start_healing() {
	local g
	exec {g}>${HEALER_MUTEX}
	local m=1
	if flock -n -x ${g}
	then
		m=0
	fi
	eval "exec ${g}>&-"

	if [[ 0 -eq $m ]]
	then
		HC=0
		if [[ 0 -lt $((Sv1 + Sv2)) ]]
		then
			Sv1=0
			Sv2=0
			push_load_sh
			show_stressing_v1_status
			show_stressing_v2_status
		fi
		reset_anomalies_ &
		HC=$!
	fi
}

function stop_healing() {
	local g
	exec {g}>${HEALER_MUTEX}
	if ! flock -n -x ${g}
	then
		rip "HC"
	fi
	eval "exec ${g}>&-"
}

################################################################################
## Starting stage

function do_prepare() {
	echo -e "\033[2J\033[HStarting"

	echo
	echo -e "${GREEN}Install python packages${NC}"
	pip3 install tabulate pandas matplotlib tensorflow==1.14.0 --upgrade --user

	echo
	echo -e "${GREEN}Read the gateway URL${NC}"
	HOST_PORT=$(bash <<URL
	A=\$(./kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
if [[ -z \$A ]]
	:
then
	A=\$(./kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
fi
P=\$(./kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.spec.ports[?(@.name=="http2")].port}')
S=\$(./kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.spec.ports[?(@.name=="https")].port}')
printf "\${A}:\${P}"
URL
)

	echo -e "${GREEN}Read the first node public address${NC}"
	POD_CARRIER=$(bash <<POD_CARRIER
a=(\$(./kubectl get nodes -o wide | head -n 2 | tail -n 1))
printf \${a[6]}
POD_CARRIER
)

	echo -e "${GREEN}Reset load.sh${NC}"
	push_load_sh

	echo -e "${GREEN}Deploy layout${NC}"
	mkdir -p ref
	mkdir -p data

	if ! [[ -d skynet ]]
	then
		echo -e "${GREEN}Deploy Skynet${NC}"
		git clone https://github.com/progmaticlab/skynet
	fi

	echo -e "${GREEN}Start the monitor${NC}"
	start_monitor

	echo -e "${GREEN}Query if learning${NC}"
	pull_learning_status

	echo -e "${GREEN}Deploy the healer${NC}"
	deploy_healer
}

do_prepare

################################################################################
## Running stage

pushd skynet > /dev/null
echo -e "\033[2J\033[HRunning"
echo -e "\033[3;0H${BOLD}INDICATORS${NC}:"
echo -e "\033[3;40H${BOLD}ANOMALIES${NC}:"
show_loading_status
show_collecting_status
show_stressing_v1_status
show_stressing_v2_status
show_training_status

while true
do
	M=("${ML[$L]}" "${MC[$C]}" "${MSv1[$Sv1]}" "${MSv2[$Sv2]}" "${MT[$T]}" "reset anomalies")
	M+=(quit)

	echo -e "\033[12;0H"
	echo Enter the number of the action:
	echo -e "\033[s"
	for ((i=0; i<= ${#M[@]}; ++i)); do echo -e "\033[K"; done
	echo -e "\033[u"

	for ((i=0; i< ${#M[@]}; ++i))
	do
		echo "$((i+1))) ${M[$i]}"
	done
	printf "#? "

	read a
	case $a in
		1)
			L=$((1 ^ L))
			toggle_loading
			show_loading_status
		;;
		2)
			C=$((1 ^ C))
			toggle_collecting
			show_collecting_status
		;;
		3)
			Sv1=$((1 ^ Sv1))
			push_load_sh
			show_stressing_v1_status
		;;
		4)
			Sv2=$((1 ^ Sv2))
			push_load_sh
			show_stressing_v2_status
		;;
		5)
			T=$((1 ^ T))
			toggle_learning
			show_training_status
		;;
		6)
			start_healing
		;;
		${#M[@]})
			printf "\033[s\033[2J\033[HStopping  \033[u"
			stop_healing
			stop_monitor
			stop_loading
			stop_collecting
			break 2
		;;
		*)
			:
		;;
	esac
done

popd > /dev/null
popd > /dev/null