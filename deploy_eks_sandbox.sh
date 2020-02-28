#!/bin/bash

set -e

NC='\e[0m'
GREEN='\e[92m'
CLUSTER_NAME=istio-demo-cluster-no1
CONTEXT_NAME=istio-demo
SSH_PUBLIC_KEY=$(find ~/.ssh/id_*.pub | head -n 1)

if [[ -z ${SSH_PUBLIC_KEY} ]]
then
	echo -e "${GREEN}Generate SSH keys${NC}"
	ssh-keygen || exit 1
	SSH_PUBLIC_KEY=$(find ~/.ssh/id_*.pub | head -n 1)
	echo
fi

echo -e "${GREEN}Install AWS cli${NC}"
pip3 install awscli --upgrade --user

if ! [[ -e ~/.aws/config ]]
then
	echo
	echo ${GREEN}Configure AWS${NC}
	~/.local/bin/aws configure
fi

echo
echo -e "${GREEN}Download kube tools for AWS${NC}"
b=$(pwd)/.sandbox/
rm -rf $b
mkdir -p $b
PATH=$PATH:$b
curl -o $b/kubectl -LO https://storage.googleapis.com/kubernetes-release/release/`curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt`/bin/linux/amd64/kubectl
curl -o $b/aws-iam-authenticator https://amazon-eks.s3-us-west-2.amazonaws.com/1.14.6/2019-08-22/bin/linux/amd64/aws-iam-authenticator
curl --silent --location "https://github.com/weaveworks/eksctl/releases/download/latest_release/eksctl_$(uname -s)_amd64.tar.gz" | tar xz -C $b

chmod u+x $b/kubectl $b/eksctl $b/aws-iam-authenticator
$b/kubectl config set-context ${CONTEXT_NAME} --cluster=${CLUSTER_NAME}
$b/kubectl config use-context ${CONTEXT_NAME}

pushd $b
echo
echo -e "${GREEN}Download istio${NC}"
curl -L https://istio.io/downloadIstio | sh -
pushd istio-*

(
	set -e

	echo
	echo -e "${GREEN}Create a EKS cluster${NC}"
	cat <<CLUSTER | $b/eksctl create cluster -f - --set-kubeconfig-context
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: ${CLUSTER_NAME}
  region: us-west-2

nodeGroups:
  - name: ${CLUSTER_NAME}-ng-no1
    instanceType: t2.xlarge
    minSize: 1
    maxSize: 1
    desiredCapacity: 1
    preBootstrapCommands:
        # Enabling the docker bridge network. We have to disable live-restore as it
        # prevents docker from recreating the default bridge network on restart
        - "echo \"\$(jq '.bridge=\"docker0\" | .\"live-restore\"=false' /etc/docker/daemon.json)\" > /etc/docker/daemon.json"
        - "systemctl restart docker"
    ssh: # import public key from file
        allow: true
        publicKeyPath: ${SSH_PUBLIC_KEY}

CLUSTER

	# read the first node internal and public addresses
	a=($($b/kubectl get nodes -o wide | head -n 2 | tail -n 1))
	h=${a[5]}
	p=${a[6]}

	echo
	echo -e "${GREEN}EKS worker node public IP: ${p}${NC}"

	echo
	echo -e "${GREEN}Configure the cluster node group${NC}"
	cat <<INSTRUCTIONS | ssh -o StrictHostKeyChecking=no -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null -i ${SSH_PUBLIC_KEY/%[.]pub/} ec2-user@$p sudo -- bash -
set -e
rpm -ivh https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm
yum -y install git python2-pip python3-pip stress.x86_64
pip install tabulate
cp /etc/docker/daemon.json /etc/docker/daemon.json.backup
echo "\$(jq '. + {"insecure-registries" : ["${h}:5000"]}' /etc/docker/daemon.json)" > /etc/docker/daemon.json
systemctl restart docker
docker run -d -p 5000:5000 --restart=always --name registry registry:2
git clone https://github.com/alexandrelevine/istio
git clone https://github.com/progmaticlab/skynet
pushd istio/samples/bookinfo
src/build-services.sh 1.1 "${h}:5000/istio"
popd
docker image ls | grep bookinfo | awk '{ print \$1":1.1" }' | xargs -n 1 docker push
H=\$(pwd)
mkdir -p /host
pushd /host
cp /usr/bin/stress .
cp \${H}/skynet/load*.sh .
cp \${H}/skynet/stress* .
dd if=/dev/zero of=disk_load.data count=1024 bs=1024
popd
chmod -R a+w /host
INSTRUCTIONS

	echo
	echo -e "${GREEN}Install istio${NC}"
	bin/istioctl manifest apply --set profile=default
	scp -o StrictHostKeyChecking=no -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null -i ${SSH_PUBLIC_KEY/%[.]pub/} ec2-user@$p:istio/samples/bookinfo/platform/kube/bookinfo.yaml .
	sed --in-place=.backup -re 's! node1:5000/! '${h}':5000/!' bookinfo.yaml

	$b/kubectl label namespace default istio-injection=enabled

	echo
	echo -e "${GREEN}Install managed application${NC}"
	$b/kubectl create -f bookinfo.yaml

	echo
	echo -e "${GREEN}Install telemetry metrics${NC}"
	$b/kubectl create -f samples/bookinfo/telemetry/metrics.yaml

	echo
	echo -e "${GREEN}Define the ingress gateway for the application${NC}"
	$b/kubectl apply -f samples/bookinfo/networking/bookinfo-gateway.yaml

	echo
	echo -e "${GREEN}Apply default destination rules${NC}"
	$b/kubectl apply -f samples/bookinfo/networking/destination-rule-all.yaml

	echo
	echo -e "${GREEN}Deploy the httpbin service${NC}"
	$b/kubectl apply -f samples/httpbin/httpbin.yaml

	echo
	echo -e "${GREEN}Determining the ingress IP and ports${NC}"
	A=$($b/kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
	if [[ -z $A ]]
	then
		A=$($b/kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
	fi
	P=$($b/kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.spec.ports[?(@.name=="http2")].port}')
	S=$($b/kubectl -n istio-system get service istio-ingressgateway -o jsonpath='{.spec.ports[?(@.name=="https")].port}')

	echo -e "${GREEN}ingress IP:${NC} ${A}"
	echo -e "${GREEN}ingress port:${NC} ${P}"
	echo -e "${GREEN}ingress secure port:${NC} ${S}"
	echo -e "${GREEN}GATEWAY_URL:${NC} ${A}:${P}"
) || (
	echo
	echo -e "${GREEN}Remove the EKS cluster${NC}"
	$b/eksctl delete cluster -n ${CLUSTER_NAME} -w
)

popd
popd