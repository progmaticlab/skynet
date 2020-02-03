#!/bin/bash

# num of CPUs
mem_cpus_default=${mem_cpus_default:-$(cat /proc/cpuinfo |grep -c processor)}
cpus_default=${cpus_default:-0}

# %  of free mem
mem_default=${mem_default:-80}

# time to load in sec
timeout_default=${timeout_default:-1}

my_file="$(readlink -e "$0")"
my_dir="$(dirname $my_file)"

function usage(){
    echo -e "Options:\n" \
         "[--mem_cpus <num>]    # number of workers for memory stress (default equals to num of CPUs in system)\n" \
         "[--mem <percent>]     # % of free mem to use in memory stress (default is 80)\n" \
         "[--cpus <num>]        # number of cpu workers (default to 0, it is for case cpu stress only w/o mem stress - mem_cpus=0)\n" \
         "[--timeout <secs>]    # time to test in seconds (default 1)\n"
}

cpus=$cpus_default
mem_cpus=$mem_cpus_default
mem=$mem_default
timeout=$timeout_default

while [[ -n "$1" ]] ; do
    case $1 in
        '--cpus')
            cpus="$2"
            ;;
        '--mem')
            mem="$2"
            ;;
        '--mem_cpus')
            mem_cpus="$2"
            ;;
        '--timeout')
            timeout="$2"
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

stress_opts=''
if [[ -n "$cpus" && "$cpus" != '0' ]] ; then
    stress_opts+="-c $cpus"
fi

if [[ -n "$mem_cpus" && "$mem_cpus" != '0' ]] ; then
    free_mem=$(free -m | awk '/Mem:/{print($4)}')
    (( mem_per_worker=free_mem*mem/mem_cpus/100 ))
    [ -n "$stress_opts" ] && stress_opts+=' '
    stress_opts+="-m $mem_cpus --vm-bytes ${mem_per_worker}M"
fi

if [ -z "$stress_opts" ] ; then
    echo "ERROR: neither cpu nor mem stress options provided"
    usage
    exit -1
fi

stress_opts+=" -t ${timeout}s"
echo "INFO: stress options: $stress_opts"
stress $stress_opts
