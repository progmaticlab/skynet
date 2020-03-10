#!/bin/bash

set -e

BOX=$(pwd)/.sandbox/
PATH=$PATH:$BOX
CLUSTER_NAME="$1"
if [[ -z "${CLUSTER_NAME}" ]] && [[ -s "${BOX}/.cluster" ]]
then
	CLUSTER_NAME=$(< ${BOX}/.cluster)
fi
if [[ -z "${CLUSTER_NAME}" ]]
then
	echo There is no cluster to delete
	exit -1
fi

${BOX}/eksctl delete cluster -n ${CLUSTER_NAME} -w
