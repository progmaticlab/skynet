# NextGen top utility

This utility is a monitoring tool which allows analysing of k8s application and its wellbeing.

## Installation

Install tabulate:

``` bash
pip install tabulate
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
./load.sh
```
