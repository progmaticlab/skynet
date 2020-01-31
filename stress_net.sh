#!/bin/bash
  
my_file="$(readlink -e "$0")"
my_dir="$(dirname $my_file)"

function usage(){
    echo -e "Shapes egress traffic\n" \
         "Options:\n" \
         "--nic <nic>           # interface name (mandatory)\n" \
         "[--rate 1]            # rate (default value 1 and unit is mbit, could be specified with unit like 100kbit)\n" \
         "[--delay 100]         # delay in ms (default 100, could be specified with unit like 1s)\n" \
         "[--loss 10]           # packet loss in % (default 0)\n" \
         "[--corrupt 10]        # corrupt level in % (default 0)\n" \
         "[--duplicate 10]      # duplicate level in % (default 0)\n" \
         "[--cleanup]           # remove all rules\n"
}

nic=''
rate='1'
delay='100'
loss='0'
corrupt='0'
duplicate='0'
cleanup=''

while [[ -n "$1" ]] ; do
    case $1 in
        '--nic')
            nic="$2"
            ;;
        '--rate')
            rate="$2"
            ;;
        '--delay')
            delay="$2"
            ;;
        '--loss')
            loss="$2"
            ;;
        '--corrupt')
            corrupt="$2"
            ;;
        '--duplicate')
            duplicate="$2"
            ;;
        '--cleanup')
            cleanup="true"
            shift 1
            continue
            ;;            
        '--help')
            usage
            exit
            ;;
        *)
            echo "ERROR: unknown options '$1'"
            usage
            exit -1
            ;;
    esac
    shift 2
done

# add measuruements units if not pointed
[[ "${delay//[^0-9]/}" == "$delay" ]] && delay+='ms'
[[ "${rate//[^0-9]/}" == "$rate" ]] && rate+='mbit'

if [ -z "$nic" ] ; then
    echo "ERORO: nic options is reqruied"
    usage
    exit -1
fi

# remove all rules if any
echo "INFO: remove all rules"
sudo tc qdisc del dev $nic root >/dev/null 2>&1 || true

if [ -n "$cleanup" ] ; then
    sudo tc qdisc show dev $nic
    exit
fi

function add_opt() {
    local dst_name=$1
    local opt=$2
    local val=$3
    [[ -n "$val" && "${val//[^0-9]/}" != 0 ]] && printf -v $dst_name "${!dst_name//\%/%%} %s %s" $opt $val
}

netem_opts=''
add_opt netem_opts delay "${delay}"
add_opt netem_opts loss "${loss}%"
add_opt netem_opts corrupt "${corrupt}%"
add_opt netem_opts duplicate "${duplicate}%"

rate_opts=''
if [ -n "$rate" ] ; then
    add_opt rate_opts rate "${rate}"
    add_opt rate_opts burst 32kbit
    add_opt rate_opts limit 3000
fi

if [[ -z "$netem_opts" && -z "$rate_opts" ]]  ; then 
    echo "ERROR: neither net delay/loss/corrupt/duplicate nor rate provided"
    usage
    exit -1  
fi

# add delay (egress traffic)
sudo tc qdisc add dev $nic root handle 1:0 netem $netem_opts

# add rate filter if eny (egress traffic)
if [ -n "$rate_opts" ] ; then
    sudo tc qdisc add dev $nic parent 1:1 handle 10: tbf $rate_opts
fi

sudo tc qdisc show dev $nic
