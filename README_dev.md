# NextGen top utility

## Utility preparation

Install tabulate:

``` bash
pip install tabulate
```

## Sample app deployment

Prepare 3 machines. Deploy Kubernetes. Let's suppose that your machines are named node1, node2, node3.

Deploy Istio for Bookinfo (do not deploy Bookinfo):
https://istio.io/docs/examples/bookinfo/

And add telemetry collection:
https://istio.io/docs/tasks/observability/metrics/collecting-metrics/

Fetch custom istio:

``` bash
git clone https://github.com/alexandrelevine/istio
```

Allow insecure docker registires (make sure that it contains the following string):

``` bash
cat /etc/docker/daemon.json
{
    "insecure-registries": ["node1:5000"]
}
```

Bring up local docker registry:

``` bash
docker run -d -p 5000:5000 --restart=always --name registry registry:2
```

Build the app and push containers (it's supposed that version 1.1 is pushed in this example):

``` bash
cd istio/samples/bookinfo
sudo src/build-services.sh 1.1 "node1:5000/istio"
sudo docker image ls | grep bookinfo | awk '{ print $1":1.1" }' | xargs -n 1 sudo docker push
```

Deploy the Bookinfo app following the guidelines (https://istio.io/docs/examples/bookinfo/) but use manifest from here:

``` bash
kubectl apply -f platform/kube/bookinfo.yaml
```

The following is to be automated:

Deploy stress utility to all of the nodes and copy it to /host:

``` bash
sudo apt-get install stress
sudo cp /usr/bin/stress /host
```

Fetch the tool, place stress utilities into /etc/host folders on all nodes:

``` bash
git clone https://github.com/alexandrelevine/envoy
sudo mkdir -p /host
sudo cp envoy/load.sh /host
sudo cp envoy/stress* /host
```

Create data file for disk load on all nodes:

``` bash
sudo dd if=/dev/zero of=/host/disk_load.data count=1024 bs=1024
```

## Usage

It's supposed that pwd is this cloned repo from where you run scripts.
As such it's suggested to create directories for data and reference file

``` bash
mkdir ../data
mkdir ../ref
```

Run data collector (in separate terminal)

``` bash
./envoy_stats.sh
```

Run monitoring utility (in separate window) and run it in maximized window to accommodate the table

``` bash
./monitor_envoy_stats.py ../data -r ../ref/refstats -p product details ratings reviews-v1 reviews-v2 reviews-v3
```

Set GATEWAY_URL as is described in bookinfo page:
https://istio.io/docs/examples/bookinfo/

Run test load (in separate terminal)

``` bash
./request.sh
```
