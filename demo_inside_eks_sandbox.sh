#!/bin/bash

BOX=$(pwd)/.sandbox/
IPC=${BOX}/.ipc

function make_ipc() {
	printf ${IPC}/$(python -c "import uuid; print(uuid.uuid1())")
}

# read env for slack
[[ -e ${BOX}/slack_env ]] && source ${BOX}/slack_env
# update env for case if env is changed
cat << EOF >${BOX}/slack_env
export SLACK_CHANNEL=\${SLACK_CHANNEL:-${SLACK_CHANNEL}}
export SHADOWCAT_BOT_TOKEN=\${SHADOWCAT_BOT_TOKEN:-${SHADOWCAT_BOT_TOKEN}}
EOF

NC='\e[0m'
RED='\e[95m'
SKYNET=${BOX}/skynet
BOLD='\e[1m'
CYAN='\e[96m'
PATH=$PATH:$BOX
GREEN='\e[92m'
CURSOR_MUTEX=$(make_ipc)
HEALER_MUTEX=$(make_ipc)
MONITOR_MUTEX=$(make_ipc)
STRESSING_MUTEX=$(make_ipc)
COLLECTING_MUTEX=$(make_ipc)
SSH_PUBLIC_KEY=$(find ~/.ssh/id_*.pub | head -n 1)
STRESSING_STATE=$(make_ipc)
MONITOR_TRANSPORT=$(make_ipc)
COLLECTING_TRANSPORT=$(make_ipc)
SLACK_COUNTERS_MUTEX=$(make_ipc)
SLACK_COUNTERS_STATE=$(make_ipc)

SLACK_APP=${BOX}/timeseries-vae-anomaly
SLACK_DATA_FOLDER=${SLACK_DATA_FOLDER:-"${BOX}/slack_app_data"}
SLACK_APP_PORT_NUMBER=${SLACK_APP_PORT_NUMBER:-8080}
SLACK_DATA_ANOMALY="${SLACK_DATA_FOLDER}/anomaly.json"
SLACK_DATA_METRICS="${SLACK_DATA_FOLDER}/metrics_0_filter.csv"
SLACK_APP_PROXY_URL=${SLACK_APP_PROXY_URL:-"http://54.214.233.135:8080"}


function rip() {
	eval "local x=\$$1"
	if [[ 0 -lt $x ]]
	then
		if kill -s 9 $x 2>/dev/null
		then
			wait $x 2>/dev/null
		fi
		eval "$1=0"
	fi
}

function make_feedback() {
	make_ipc
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
print('Start json2csv_metrics...')
with open('$src', 'r') as fs:
	data = json.load(fs)
	if (len(data) == 0):
		print('done - nothing to do')
		sys.exit(-1)
	with open('$dst', 'w') as fd:
		d = csv.writer(fd)
		keys = list(data.keys())
		d.writerow(keys)
		index = 0
		max_index = len(data[keys[0]]['ts'])
		for k in keys:
			nm = len(data[k]['ts'])
			if nm < max_index:
				max_index = nm
				print('WARN: max_index: %s: replace %s with ' % (k, max_index, nm))
		while (index < max_index):
			row = [index]
			for k in keys:
				row.append(data[k]['ts'][index])
			d.writerow(row)
			index += 1
print('done')
sys.exit(0)
" >> $dbg_dir/json2csv_metrics.log 2>&1 || ret=1
	if [[ $ret != 0 ]] ; then
		# save bad file if error for next dbg
		cp $src $dbg_dir/src.json
		[ -f $dst ] && cp $dst $dbg_dir/dst.csv
	fi
	echo "ret code: $ret" >> $dbg_dir/json2csv_metrics.log 2>&1 
	return $ret
}


B=(no yes)

################################################################################
## Loading block

LC=0
function stop_loading() {
	echo "stop pid=$LC" >> $BOX/requests.log
	rip "LC"
	echo "stop pid=$LC done" >> $BOX/requests.log
}

function show_loading_stats() {
	echo -e "show_loading_stats:\n$@" >> $BOX/requests.log
	# local i=4
	# local msg=
	# echo -e "$@" | while read msg  ; do
	# 	printf "\033[s\033[$i;80H${CYAN}${msg}${NC}\033[u"
	# 	(( i+=1 ))
	# done
}

function do_loading() {
	while true; do
		local stat=$(GATEWAY_URL="$HOST_PORT" ${SKYNET}/request.sh 10 2>&1 | awk '/time_average:/ {print("aver_rq_time: "$4"\nerrors: "$6"\nreqs: "$2)}')
		show_loading_stats "$stat"
	done
}

function start_loading() {
	do_loading &
	LC=$!
	echo "start pid=$LC" >> $BOX/requests.log
}

################################################################################
## Collecting block

CC=0
function do_toggle_collecting() {
	if [[ 1 -eq $1 ]]
	then
		pushd ${BOX}/data >/dev/null
		${SKYNET}/envoy_stats.sh 2>/dev/null 1>&2 &
		CC=$!
		popd > /dev/null

		start_loading
	else
		stop_loading
		rip "CC"
	fi
}

function show_collecting_status() {
	printf "\033[s\033[4;2H${CYAN}Collecting${NC}: %-5s\033[u" ${B[$1]}
}

CM=0
function track_collecting() {
	local a
	local s=0
	local i=$1

	while read -N 1 -u $i a
	do
		case $a in
			1)
				s=1
			;;
			2)
				s=0
			;;
			3)
				s=$((1 ^ s))
			;;
			4)
				break 2
			;;
		esac
		do_toggle_collecting $s
		protect_cursor show_collecting_status $s
	done

	do_toggle_collecting 0
}

function start_collecting_monitor() {
	local t

	rm -f ${COLLECTING_TRANSPORT}
	mkfifo ${COLLECTING_TRANSPORT}
	exec {t}<> ${COLLECTING_TRANSPORT}

	track_collecting $t &

	COLLECTING_CHANNEL=$t
	CM=$!
}

function stop_collecting_monitor() {
	tell_collecting_monitor 4
	if [[ 0 -lt $CM ]]
	then
		wait $CM 2>/dev/null
		CM=0
		exec {COLLECTING_CHANNEL}>&-
		COLLECTING_CHANNEL=0
	fi
}

function tell_collecting_monitor() {
	local c=$1
	if [[ 0 -lt ${CM} ]]
	then
		local g
		exec {g}<${COLLECTING_MUTEX}
		flock -x ${g}
		printf "$c" >&${COLLECTING_CHANNEL}
		exec {g}<&-
	fi
}

function stop_collecting() {
	tell_collecting_monitor 2
}

function start_collecting() {
	tell_collecting_monitor 1
}

function toggle_collecting() {
	tell_collecting_monitor 3
}

################################################################################
## Stress v# block

function show_stressing_v1_status() {
	printf "\033[s\033[5;2H${CYAN}Stressing reviews-v1${NC}: %-5s\033[u" ${B[$1]}
}

function show_stressing_v2_status() {
	printf "\033[s\033[6;2H${CYAN}Stressing reviews-v2${NC}: %-5s\033[u" ${B[$1]}
}

function toggle_stressing() {
	cat <<STATE > ${STRESSING_STATE}
Sv1=$1
Sv2=$2
STATE
	cat <<INSTRUCTIONS | ssh -o StrictHostKeyChecking=no -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null -i ${SSH_PUBLIC_KEY/%[.]pub/} ec2-user@$POD_CARRIER sudo -- bash - 2>/dev/null
cat <<LOADSH > /host/load.sh
#!/bin/bash
Sv1=$1
Sv2=$2
LOADSH
cat <<'LOADSH' >> /host/load.sh
s=\${SERVERDIRNAME}-\${SERVICE_VERSION}
echo "\$s: load \$1" \$(date +"%T.%N") >> /host/load_time.log
if [[ 1 -eq \${Sv1} ]] && [[ "\${SERVICE_VERSION}" == 'v1' ]]
then
	echo "\$s: SLEEP" \$(date +"%T.%N") >> /host/load_time.log
	sleep 0.1
fi
if [[ 1 -eq \${Sv2} ]] && [[ "\${SERVICE_VERSION}" == 'v2' ]]
then
	echo "\$s: SLEEP" \$(date +"%T.%N") >> /host/load_time.log
	sleep 0.15
fi
echo "\$s: load \$1" \$(date +"%T.%N") >> /host/load_time.log
LOADSH
INSTRUCTIONS
}

function down_stressing_v1() {
	local g
	(
		flock -x ${g}
		. ${STRESSING_STATE}
		if [[ 1 -eq $Sv1 ]]
		then
			toggle_stressing 0 $Sv2
			protect_cursor show_stressing_v1_status 0
		fi
	) {g}> ${STRESSING_MUTEX}
}

function toggle_stressing_v1() {
	local g
	(
		flock -x ${g}
		. ${STRESSING_STATE}
		Sv1=$((1 ^ Sv1))
		toggle_stressing $Sv1 $Sv2
		protect_cursor show_stressing_v1_status $Sv1
	) {g}> ${STRESSING_MUTEX}
}

function down_stressing_v2() {
	local g
	(
		flock -x ${g}
		. ${STRESSING_STATE}
		if [[ 1 -eq $Sv2 ]]
		then
			toggle_stressing $Sv1 0
			protect_cursor show_stressing_v2_status 0
		fi
	) {g}> ${STRESSING_MUTEX}
}

function toggle_stressing_v2() {
	local g
	(
		flock -x ${g}
		. ${STRESSING_STATE}
		Sv2=$((1 ^ Sv2))
		toggle_stressing $Sv1 $Sv2
		protect_cursor show_stressing_v2_status $Sv2
	) {g}> ${STRESSING_MUTEX}
}

function deploy_stressing() {
	local g
	(
		flock -x ${g}
		toggle_stressing 0 0
	) {g}> ${STRESSING_MUTEX}
}

function show_stressing_status() {
	local g
	(
		flock -x ${g}
		. ${STRESSING_STATE}

		protect_cursor show_stressing_v1_status $Sv1
		protect_cursor show_stressing_v2_status $Sv2
	) {g}> ${STRESSING_MUTEX}
}

################################################################################
## Training block

T=0
MT=('train' 'stop training')
function show_training_status() {
	printf "\033[s\033[7;2H${CYAN}Learning${NC}: %-5s\033[u" ${B[$T]}
}

if [[ ! -d $BOX ]] ; then
	echo "ERROR: there is no $BOX folder. Please run deploy first."
	exit -1
fi
pushd $BOX > /dev/null

################################################################################
## Slack app block

MS=0

function save_the_state() {
	cat <<STATE > ${SLACK_COUNTERS_STATE}
REPORTS=$1
REPLACEMENTS=$2
STATE
}

function start_slack_app() {
	local g
	(
		flock -x $g
		save_the_state 0 0
	) {g}> ${SLACK_COUNTERS_MUTEX}

	mkdir -p $SLACK_DATA_FOLDER
	SLACK_CHANNEL=$SLACK_CHANNEL \
	SHADOWCAT_BOT_TOKEN=$SHADOWCAT_BOT_TOKEN \
	HOST_NAME='localhost' \
	PORT_NUMBER=$SLACK_APP_PORT_NUMBER \
	DATA_FOLDER=$SLACK_DATA_FOLDER \
	SAMPLES_FOLDER=$SLACK_DATA_FOLDER \
		python3 -u ${SLACK_APP}/src/server.py >>${BOX}/slack_app_server.log 2>&1 &
	MS=$!
}

function stop_slack_app() {
	echo "stopping slack app... pid=$MS"
	rip "MS"
	echo "finished"
}

function bump_slack_reported() {
	local g
	(
		flock -x $g
		. ${SLACK_COUNTERS_STATE}
		printf "\033[s\033[9;2H${CYAN}Slack reports${NC}: %-5s\033[u" ${REPORTS}
		save_the_state $((++REPORTS)) ${REPLACEMENTS}

	) {g}> ${SLACK_COUNTERS_MUTEX}
}

function bump_slack_replaced() {
	local g
	(
		flock -x $g
		. ${SLACK_COUNTERS_STATE}
		printf "\033[s\033[10;2H${CYAN}Slack replaced${NC}: %-5s\033[u" ${REPLACEMENTS}
		save_the_state ${REPORTS} $((++REPLACEMENTS))

	) {g}> ${SLACK_COUNTERS_MUTEX}
}

function send_anomalies_info_to_slackapp() {
	query_anomalies_data query_anomalies_info $SLACK_DATA_ANOMALY
	if json2csv_metrics $SLACK_DATA_ANOMALY $SLACK_DATA_METRICS ; then
		local suffix=$(date +%s)
		cp ${SLACK_DATA_ANOMALY} ${SLACK_DATA_ANOMALY}.$suffix >/dev/null 2>&1
		cp ${SLACK_DATA_METRICS} ${SLACK_DATA_METRICS}.$suffix >/dev/null 2>&1
		echo "curl -m 5 -s http://localhost:${SLACK_APP_PORT_NUMBER}/analysis/run" >>${BOX}/slack_app_client.log
		curl -m 5 -s http://localhost:${SLACK_APP_PORT_NUMBER}/analysis/run >>${BOX}/slack_app_client.log 2>&1
		bump_slack_reported
	fi
}

function process_responses_from_slack() {
	local request_response_url="${SLACK_APP_PROXY_URL}/analysis/response/$SLACK_CHANNEL"
	echo "curl -m 5 -s $request_response_url" >>${BOX}/slack_app_client.log
	local data=$(curl -m 5 -s $request_response_url 2>>${BOX}/slack_app_client.log)
	echo "read data: $data" >> ${BOX}/slack_app_client.log
	local action=$(echo $data | cut -d ':' -f 1)
	if [[ -n "$action" ]] ; then
		# echo "curl -s http://localhost:${SLACK_APP_PORT_NUMBER}/slack/command/$action" >>${BOX}/slack_app_client.log
		# curl -s http://localhost:${SLACK_APP_PORT_NUMBER}/slack/command/$action >>${BOX}/slack_app_client.log 2>&1
		local report_action_url="http://localhost:${SLACK_APP_PORT_NUMBER}/slack/command/$action"
		echo curl -m 5 -s \
			-X POST -H  "Content-Type: application/json" \
			--data "payload={\"actions\": [ {\"value\": \"$data\"} ]}" \
			$report_action_url >>${BOX}/slack_app_client.log 2>&1
		curl -m 5 -s \
			-X POST -H  "Content-Type: application/json" \
			--data "payload={\"actions\": [ {\"value\": \"$data\"} ]}" \
			$report_action_url >>${BOX}/slack_app_client.log 2>&1
		if [[ 'suggestion_1_on' == "$action" ]] ; then
			local pod=$(echo $data | cut -d ':' -s -f 2)
			echo "restart pod $pod" >> ${BOX}/slack_app_client.log
			do_pod_restart $pod /dev/null
			bump_slack_replaced
		fi
	fi
}

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
	printf "{\"command\": \"query_load\", \"promise\": \"${f}\"}\0"
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
	local a=($(printf '%s' $j | jq '.pods[].name' | tr -d '"'))
	local b=($(printf '%s' $j | jq '.pods[].ordinary'))
	local c=($(printf '%s' $j | jq '.pods[].ml_confirmed'))

	for((i=0;i<10;++i))
	do
		local k=$((i + 4))
		printf "\033[s\033[${k};42H\033[K\033[u"
	done
	for i in ${!a[*]}
	do
		local k=$((i + 4))
#		printf "\033[s\033[${k};42H${RED}${a[$i]}${NC}: ordinary(${BOLD}${b[$i]}${NC}), ml(${BOLD}${c[$i]}${NC})\033[u"
		printf "\033[s\033[${k};42H${RED}%-32s${NC}${BOLD}%6d${NC}\033[u" "${a[$i]}:" ${c[$i]}
	done
	a=$(printf '%s' $j | jq '.samples.total')
#	b=$(printf '%s' $j | jq '.samples.ref')
	printf "\033[s\033[8;2H${CYAN}Total samples${NC}: %-5d\033[u" $a
#	printf "\033[s\033[10;2H${CYAN}Ref samples${NC}: ${b}  \033[u"
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
		down_stressing_v1
		down_stressing_v2
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
		process_responses_from_slack
		sleep 3
	done
}

function start_monitor() {
	rm -f ${MONITOR_TRANSPORT}
	mkfifo ${MONITOR_TRANSPORT}
	${SKYNET}/monitor_envoy_stats.py ${BOX}/data -r ${BOX}/ref/refstats -B -p product details ratings reviews-v1 reviews-v2 reviews-v3 2> /dev/null 1>&2 < ${MONITOR_TRANSPORT} &
	MX=$!
	local p
	exec {p}> ${MONITOR_TRANSPORT}
	MONITOR_CHANNEL=$p

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
		(
			sleep 5
			kill -s 9 $MX 2>/dev/null
		) &
		local k=$!
		wait $MX 2>/dev/null
		kill -s 9 $k 2>/dev/null
		wait $k 2>/dev/null

		MX=0
		MONITOR_CHANNEL=0
	fi
}

################################################################################
## Restart pod block

function do_pod_restart() {
	local p=$1
	case $p in
		reviews-v1-*)
			down_stressing_v1
		;;
		reviews-v2-*)
			down_stressing_v2
		;;
	esac

	stop_collecting
	echo ${BOX}/kubectl delete pod $p >> ${BOX}/kube.log
	${BOX}/kubectl delete pod $p 2>> ${BOX}/kube.log 1>&2
	sleep 5
	start_collecting
	echo 1 >& $2

	reset_pod_service $p
	reset_anomalies
}

function show_job_progress() {
	local j=$1
	shift
	local z=(/ '\u2014' \\ \| / '\u2014' \\ \|)
	local k=$((j % ${#z[@]}))
	printf "\033[s\033[3;51H\033[K\033[3;51H$* %b\033[u" ${z[k]}
}

function wipe_job_progress() {
	printf "\033[s\033[3;50H\033[K\033[u"
}

function conduct_pod_restart() {
	local g
	exec {g}>${HEALER_MUTEX}
	flock -x ${g}

	local d
	local f=$(make_feedback)
	mkfifo $f
	exec {d}<> $f

	do_pod_restart $1 $d &
	local c=$!

	local j=0
	while ! read -t 0 -u $d
	do
		protect_cursor show_job_progress $j "replacing pod $1"
		usleep 150000
		((++j))
	done
	exec {d}<&-
	wait $c 2>/dev/null
	rm -f $f

	protect_cursor wipe_job_progress
}

XC=0
function start_pod_restart() {
	# make sure this will be the only restart job
	local g
	exec {g}>${HEALER_MUTEX}
	flock -x ${g}
	exec {g}>&-

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
	query_anomalies | jq '.anomalies[].name' | tr -d '"'
}

function show_restart_pod_menu() {
	printf "\033[13;0H\033[KEnter the number of the action:\n\n"
	for ((i=0; i< 15; ++i));
	do
		printf "\033[$((15 + i));0H\033[K"
	done

	local a=($(list_restart_eligible_pods))
	MR=()
	for i in ${a[@]}; do MR+=("replace pod $i"); done
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
## reset everything block

function cleanup_data() {
	local suffix=$(date +"%T.%N")
	mkdir -p ${BOX}/archive
	mv ${BOX}/data ${BOX}/archive/data.$suffix
	mv ${BOX}/ref ${BOX}/archive/ref.$suffix
	mkdir -p ${BOX}/ref
	mkdir -p ${BOX}/data
}

function reset_everything() {
	stop_collecting

	T=0
	toggle_learning
	protect_cursor show_training_status

	reset_anomalies
	cleanup_data
	stop_monitor
	start_monitor

	T=1
	pull_learning_status
	protect_cursor show_training_status
}

################################################################################
## Starting stage

HC=
function collapse() {
	abort_pod_restart
	stop_monitor
	stop_loading
	stop_collecting_monitor
	stop_slack_app
	rip "HC"
}

function show_stopping_status() {
	printf "\033[s\033[2J\033[HStopping  \033[u\n"
}

function do_quit() {
	protect_cursor show_stopping_status
	collapse
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

function deploy_layout() {
	rm -rf ${IPC}
	mkdir -p ref
	mkdir -p data
	mkdir -p ${IPC}
	touch ${CURSOR_MUTEX}
	touch ${HEALER_MUTEX}
	touch ${MONITOR_MUTEX}
	touch ${STRESSING_MUTEX}
	touch ${COLLECTING_MUTEX}
}

function do_prepare() {
	echo -e "\033[2J\033[HStarting"

	echo
	echo -e "${GREEN}Deploy layout${NC}"
	deploy_layout

	if ! which jq > /dev/null 2>&1 ; then
		sudo yum install -y jq
	fi
	local pip3cmd='pip3'
	if ! which $pip3cmd  > /dev/null 2>&1 ; then
		pip3cmd='pip-3.6'
		if ! which $pip3cmd  > /dev/null 2>&1 ; then
			echo "ERROR: Unsupported image."
			echo "       Please install manually pip3 and/or provide availability pip3 cmd"
			exit -1
		fi
	fi
	$pip3cmd list 2>/dev/null | (
		declare -A m=(["tabulate"]= ["pandas"]= ["matplotlib"]= ["tensorflow"]=1.14.0 ["slackclient"]= ["requests"]=)
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
			echo
			echo -e "${GREEN}Install python packages${NC}"
			echo "$pip3cmd install$x --upgrade --user"
			$pip3cmd install$x --upgrade --user
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
	echo -e "Detected: ${HOST_PORT}\n"

	echo -e "${GREEN}Read the first node public address${NC}"
	POD_CARRIER=$(bash <<POD_CARRIER
a=(\$(./kubectl get nodes -o wide | head -n 2 | tail -n 1))
printf \${a[6]}
POD_CARRIER
)
	echo -e "Detected: ${POD_CARRIER}\n"

	echo -e "${GREEN}Reset load.sh${NC}"
	deploy_stressing

	if ! [[ -d skynet ]]
	then
		echo -e "${GREEN}Deploy Skynet${NC}"
		git clone https://github.com/progmaticlab/skynet
	else 
		pushd skynet
		git pull
		popd
	fi

	if ! [[ -d timeseries-vae-anomaly ]]
	then
		echo -e "${GREEN}Deploy timeseries-vae-anomaly${NC}"
		git clone https://github.com/progmaticlab/timeseries-vae-anomaly
	else 
		pushd timeseries-vae-anomaly
		git pull
		popd
	fi

	echo -e "${GREEN}Start the monitor${NC}"
	start_monitor
	start_collecting_monitor

	echo -e "${GREEN}Start the slack app${NC}"
	start_slack_app

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
	MM=("toggle collecting" "toggle stressing reviews-v1")
	MM+=("toggle stressing reviews-v2" "${MT[$T]}")
	MM+=("reset anomalies" "replace unhealthy pod" "reset all data" quit)

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
	while true
	do
		protect_cursor show_main_menu

		read a
		stty -echo
		case $a in
			1)
				toggle_collecting
			;;
			2)
				toggle_stressing_v1
			;;
			3)
				toggle_stressing_v2
			;;
			4)
				T=$((1 ^ T))
				toggle_learning
				protect_cursor show_training_status
			;;
			5)
				reset_anomalies
			;;
			6)
				stty echo
				show_restart_pod_dialog
			;;
			7)
				reset_everything
			;;
			${#MM[@]})
				stty echo
				do_quit
				break 2
			;;
			*)
				:
			;;
		esac
		stty echo
	done
}

printf "\033[2J\033[HRunning (Slack channel: ${BOLD}%s${NC})\n" ${SLACK_CHANNEL}
echo -e "\033[3;0H${BOLD}INDICATORS${NC}:"
echo -e "\033[3;40H${BOLD}ANOMALIES${NC}:"
# echo -e "\033[3;80H${BOLD}LOAD STATS${NC}:"
stop_collecting
show_stressing_status
show_training_status
bump_slack_reported
bump_slack_replaced
show_main_menu_dialog

popd > /dev/null
