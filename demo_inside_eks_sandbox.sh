#!/bin/bash

NC='\e[0m'
BOX=$(pwd)/.sandbox/
RED='\e[95m'
SKYNET=${BOX}/skynet
BOLD='\e[1m'
CYAN='\e[96m'
PATH=$PATH:$BOX
GREEN='\e[92m'
CURSOR_MUTEX=$BOX/.cursor
HEALER_MUTEX=$BOX/.healer
MONITOR_MUTEX=$BOX/.monitor
SSH_PUBLIC_KEY=$(find ~/.ssh/id_*.pub | head -n 1)
MONITOR_TRANSPORT=$BOX/.transport

SLACK_DATA_FOLDER=${SLACK_DATA_FOLDER:-'./ml_data'}
SLACK_APP_PORT_NUMBER=${SLACK_APP_PORT_NUMBER:-8080}

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

function make_feedback() {
	printf $BOX/fifo/$(make_uuid)
}

function protect_cursor() {
	local g
	exec {g}> ${CURSOR_MUTEX}
	flock -x $g
	$*
	eval "exec ${g}>&-"
}

function json2csv_metrics() {
	local src=$1
	local dst=$2
	local dbg_dir=$BOX/json2csv_metrics
	mkdir -p $dbg_dir
	local ret=0
	python -c "
import json, csv, sys
with open('$src', 'r') as fs:
	data = json.load(fs)
	if (len(data) == 0):
		sys.exit(-1)
	with open('$dst', 'w') as fd:
		d = csv.writer(fd)
		keys = list(data.keys())
		d.writerow(keys)
		index = 0
		max_index = len(data[keys[0]]['ts'])
		while (index < max_index):
			row = [index]
			for k in keys:
				row.append(data[k]['ts'][index])
			d.writerow(row)
			index += 1
" >> $dbg_dir/json2csv_metrics.log 2>&1 || ret=1
	if [[ $ret != 0 ]] ; then
		cp $src $dbg_dir/src.json
		[ -f $dst ] && cp $dst $dbg_dir/dst.csv
	fi
	return $ret
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
		stop_loading
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
	sleep 2
fi
if [[ 1 -eq \${Sv2} ]] && [[ "\${SERVICE_VERSION}" == 'v2' ]]
then
	echo "\$s: SLEEP" \$(date +"%T.%N") >> /host/load_time.log
	sleep 3
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
function protect_monitor() {
	if [[ 0 -lt ${MX} ]]
	then
		local g
		exec {g}<${MONITOR_MUTEX}
		flock -x ${g}
		$* >&${MONITOR_CHANNEL}
		eval "exec ${g}<&-"
	fi
}

function query_anomalies_() {
	local f=$1
	printf "{\"command\": \"list_anomalies\", \"promise\": \"${f}\"}\0"
}

function query_anomalies() {
	if [[ 0 -eq ${MX} ]]; then return -1; fi

	local f=$(make_feedback)
	mkfifo $f

	protect_monitor query_anomalies_ $f

	local j=$(< $f)
	rm -f $f

	printf '%s' $j
}

function show_anomalies() {
	local j=$1
	local a=($(printf '%s' $j | jq '.[].name' | tr -d '"'))
	local b=($(printf '%s' $j | jq '.[].ordinary'))
	local c=($(printf '%s' $j | jq '.[].ml_confirmed'))

	for((i=0;i<8;++i))
	do
		local k=$((i + 4))
		printf "\033[s\033[${k};42H\033[K\033[u"
	done
	for i in ${!a[*]}
	do
		local k=$((i + 4))
		printf "\033[s\033[${k};42H${RED}${a[$i]}${NC}: ordinary(${BOLD}${b[$i]}${NC}), ml(${BOLD}${c[$i]}${NC})\033[u"
	done
}

function query_anomalies_data_() {
	local c=$1
	local f=$2
	printf "{\"command\": \"${c}\", \"promise\": \"${f}\"}\0"
}

function query_anomalies_data() {
	if [[ 0 -eq ${MX} ]]; then return -1; fi
	local cmd=$1
	local dst=$2

	local f=$(make_feedback)
	mkfifo $f

	protect_monitor query_anomalies_data_ $cmd $f

	cat < $f > $dst
	rm -f $f
}

function send_anomalies_info_to_slackapp() {
	local f1=$(make_feedback)
	local f2=$(make_feedback)
	query_anomalies_data query_anomalies_info $f1
	if json2csv_metrics $f1 $f2 ; then
		scp -o StrictHostKeyChecking=no -o GlobalKnownHostsFile=/dev/null \
			-o UserKnownHostsFile=/dev/null -i ${SSH_PUBLIC_KEY/%[.]pub/} \
			$f1 ec2-user@$POD_CARRIER:~/ml_data/anomaly.json >/dev/null 2>&1
		scp -o StrictHostKeyChecking=no -o GlobalKnownHostsFile=/dev/null \
			-o UserKnownHostsFile=/dev/null -i ${SSH_PUBLIC_KEY/%[.]pub/} \
			$f2 ec2-user@$POD_CARRIER:~/ml_data/metrics_0_filter.csv >/dev/null 2>&1
		ssh -o StrictHostKeyChecking=no -o GlobalKnownHostsFile=/dev/null \
			-o UserKnownHostsFile=/dev/null -i ${SSH_PUBLIC_KEY/%[.]pub/} \
			ec2-user@$POD_CARRIER curl http://localhost:${SLACK_APP_PORT_NUMBER}/analysis/run >/dev/null 2>&1
	fi
	rm -f $f1 $f2
}

function pull_anomalies() {
	local j=$(query_anomalies)
	if [[ 0 -eq $? ]]
	then
		protect_cursor show_anomalies $j
		send_anomalies_info_to_slackapp
	fi
}

function pull_learning_status_() {
	local f=$1
	printf "{\"command\": \"is_learning\", \"promise\": \"${f}\"}\0"
}

function pull_learning_status() {
	if [[ 0 -lt ${MX} ]]
	then
		local f=$(make_feedback)
		mkfifo $f

		protect_monitor pull_learning_status_ $f

		local j=$(< $f)
		rm -f $f
		local a=($(printf '%s' $j | jq '.learning'))
		case "$a" in
			true) T=1;;
			*) T=0;;
		esac
	fi
}

function toggle_learning_() {
	printf "{\"command\": \"toggle_learning\"}\0"
}

function toggle_learning() {
	protect_monitor toggle_learning_
}

function reset_anomalies_() {
	printf "{\"command\": \"reset_anomalies\"}\0"
 }
 
function reset_anomalies() {
	if [[ 0 -lt ${MX} ]]
	then
		if [[ 0 -lt $((Sv1 + Sv2)) ]]
		then
			Sv1=0
			Sv2=0
			push_load_sh
			protect_cursor show_stressing_v1_status
			protect_cursor show_stressing_v2_status
		fi
		protect_monitor reset_anomalies_
	fi
}

function reset_pod_service_() {
	local p=$1
	printf "{\"command\": \"reset_pod_service\", \"pod\": \"${p}\"}\0"
}

function reset_pod_service() {
	protect_monitor reset_pod_service_ $1
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

function stop_monitor_() {
	printf "{\"command\": \"quit\"}\0"
}

function stop_monitor() {
	if [[ 0 -lt $MX ]]
	then
		rip "MY"

		protect_monitor stop_monitor_

		wait $MX 2>/dev/null
		MX=0
	fi
}

################################################################################
## Restart pod block

function do_pod_restart() {
	echo ${BOX}/kubectl delete pod $1 >> ${BOX}/kube.log
	${BOX}/kubectl delete pod $1 2>> ${BOX}/kube.log 1>&2
#	sleep 10
	echo 1 >& $2

	reset_pod_service $1
}

function show_job_progress() {
	local j=$1
	shift
	local z=(/ '\u2014' \\ \| / '\u2014' \\ \|)
	local k=$((j % ${#z[@]}))
	printf "\033[s\033[3;51H\033[K\033[3;51H$* %b\033[u" ${z[k]}
}

function conduct_pod_restart() {
	local g
	exec {g}>${HEALER_MUTEX}
	flock -x ${g}

	local f=$(make_feedback)
	mkfifo $f
	exec {d}<> $f
	do_pod_restart $1 $d &
	local c=$!

	local j=0
	while ! read -t 0 -u $d
	do
		protect_cursor show_job_progress $j "deleting pod $1"
		usleep 150000
		((++j))
	done
	exec {d}<&-
	wait $c 2>/dev/null
	rm -f $f

	protect_cursor printf "\033[s\033[3;50H\033[K\033[u"
}

XC=0
function start_pod_restart() {
	# make sure this will be the only restart job
	local g
	exec {g}>${HEALER_MUTEX}
	flock -x ${g}
	eval "exec ${g}>&-"

	conduct_pod_restart $1 &
	XC=$!
}

function abort_pod_restart() {
	local g
	exec {g}>${HEALER_MUTEX}
	if ! flock -n -x ${g}
	then
		rip "XC"
	fi
	eval "exec ${g}>&-"
}

MR=()
function list_restart_eligible_pods() {
	query_anomalies | jq '.[].name' | tr -d '"'
#	echo "aaa" "bbb" "ccc"
}

function show_restart_pod_menu() {
	printf "\033[13;0H\033[KEnter the number of the action:\n\n"
	for ((i=0; i< 15; ++i));
	do
		printf "\033[$((15 + i));0H\033[K"
	done

	local a=($(list_restart_eligible_pods))
	MR=()
	for i in ${a[@]}; do MR+=("delete pod $i"); done
	MR+=(back)
	for ((i=0; i< ${#MR[@]}; ++i));
	do
		printf "\033[$((15 + i));0H\033[K%b\n" "$((i+1))) ${MR[$i]}"
	done
	printf "\033[K#? "
}

function show_restart_pod_dialog() {
	local v
	local q=1
	declare -A m=()
	while [[ 1 -eq $q ]]
	do
		protect_cursor show_restart_pod_menu
		local I=(${!MR[@]})
		for ((i=1; i<= ${#I[@]}; ++i))
		do
			m["$i"]=1
		done

		read v
		stty -echo
		case $v in
			${#MR[@]})
				q=0
			;;
			*)
				if [[ -n "${m[$v]}" ]]
				then
					local n=(${MR[$((v - 1))]})
					start_pod_restart ${n[2]}
					q=0
				fi
			;;
		esac
		stty echo
	done
}

################################################################################
## Starting stage

HC=
function collapse() {
	rip "HC"
	abort_pod_restart
	stop_monitor
	stop_loading
	stop_collecting
}

function care_parent() {
	local p=$1
	while true
	do
		sleep 2
		if ! kill -s 0 $p 2>/dev/null
		then
			collapse
			kill -s 9 -$p 2>/dev/null
			break
		fi
	done
}

function do_prepare() {
	echo -e "\033[2J\033[HStarting"

	echo
	pip3 list 2>/dev/null | (
		declare -A m=(["tabulate"]= ["pandas"]= ["matplotlib"]= ["tensorflow"]=1.14.0)
		while read x
		do
			y=($x)
			z=${y[0]}
			if [[ ${m[$z]+_} ]]
			then
				unset m[$z]
			fi
		done
		x=
		for i in ${!m[@]}
		do
			x="$x $i"
			y=${m[$i]}
			if [[ "$y" ]]
			then
				x="$x==$y"
			fi
		done
		if [[ "$x" ]]
		then
			echo -e "${GREEN}Install python packages${NC}"
			echo "pip3 install$x --upgrade --user"
			pip3 install$x --upgrade --user
			echo
		fi
	)
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
	rm -rf fifo
	mkdir -p ref
	mkdir -p data
	mkdir -p fifo
	touch ${HEALER_MUTEX}
	touch ${CURSOR_MUTEX}
	touch ${MONITOR_MUTEX}

	if ! [[ -d skynet ]]
	then
		echo -e "${GREEN}Deploy Skynet${NC}"
		git clone https://github.com/progmaticlab/skynet
	fi

	echo -e "${GREEN}Start the monitor${NC}"
	start_monitor

	echo -e "${GREEN}Query if learning${NC}"
	pull_learning_status

	local t=$$
	care_parent $t &
	HC=$!
}

do_prepare

################################################################################
## Running stage

MM=()
function show_main_menu() {
	MM=("${ML[$L]}" "${MC[$C]}" "${MSv1[$Sv1]}" "${MSv2[$Sv2]}" "${MT[$T]}")
	MM+=("reset anomalies" "delete unhealthy pod" quit)

	printf "\033[13;0H\033[KEnter the number of the action:\n\n"
	for ((i=0; i< ${#MM[@]}; ++i));
	do
		printf "\033[$((15 + i));0H\033[K%b\n" "$((i+1))) ${MM[$i]}"
	done
	printf "\033[K#? "
}

function show_main_menu_dialog() {
	local a
	local g
	touch ${CURSOR_MUTEX}
	while true
	do
		protect_cursor show_main_menu

		read a
		stty -echo
		case $a in
			1)
				L=$((1 ^ L))
				toggle_loading
				protect_cursor show_loading_status
			;;
			2)
				C=$((1 ^ C))
				toggle_collecting
				protect_cursor show_collecting_status
			;;
			3)
				Sv1=$((1 ^ Sv1))
				push_load_sh
				protect_cursor show_stressing_v1_status
			;;
			4)
				Sv2=$((1 ^ Sv2))
				push_load_sh
				protect_cursor show_stressing_v2_status
			;;
			5)
				T=$((1 ^ T))
				toggle_learning
				protect_cursor show_training_status
			;;
			6)
				reset_anomalies
			;;
			7)
				stty echo
				show_restart_pod_dialog
			;;
			${#MM[@]})
				stty echo
				protect_cursor printf "\033[s\033[2J\033[HStopping  \033[u"
				collapse
				break 2
			;;
			*)
				:
			;;
		esac
		stty echo
	done
}

pushd skynet > /dev/null
echo -e "\033[2J\033[HRunning"
echo -e "\033[3;0H${BOLD}INDICATORS${NC}:"
echo -e "\033[3;40H${BOLD}ANOMALIES${NC}:"
show_loading_status
show_collecting_status
show_stressing_v1_status
show_stressing_v2_status
show_training_status
show_main_menu_dialog

popd > /dev/null
popd > /dev/null
