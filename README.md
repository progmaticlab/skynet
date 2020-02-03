# NextGen top utility

This utility is a monitoring tool which allows analysing of k8s application and its wellbeing.

## Utility preparation

Install tabulate:

``` bash
pip install tabulate
```

## Sample app deployment

Prepare 3 machines. Deploy Kubernetes. Let's suppose that your machines are named node1, node2, node3.

Deploy Istio for Bookinfo (do not deploy Bookinfo):
https://istio.io/docs/examples/bookinfo/

Fetch custom istio:

``` bash
git clone https://github.com/alexandrelevine/istio
```

Bring up local docker registry:

``` bash
docker run -d -p 5000:5000 --restart=always --name registry registry:2
```

Build the app:

``` bash
cd istio/samples/bookinfo
sudo src/build-services.sh 1.1 "node1:5000/istio" 
```

Deploy the Bookinfo app following the guidelines (https://istio.io/docs/examples/bookinfo/) but use manifest from here:

``` bash
kubectl apply -f platform/kube/bookinfo.yaml
```

The following is to be automated:

Deploy stress utility to all of the nodes:

``` bash
sudo apt-get install stress
```

Fetch the tool and place stress utilities into /etc/host folders on all nodes:

``` bash
git clone https://github.com/alexandrelevine/envoy
sudo mkdir -p /etc/host
sudo cp envoy/load.sh /etc/host
sudo cp envoy/stress* /etc/host
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

Run test load (in separate terminal)

``` bash
./request.sh
```
