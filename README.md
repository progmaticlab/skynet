# NextGen top utility

This utility is a monitoring tool which allows analysing of k8s application and its wellbeing.

## Prepare a VM for demo application
It could be Amazon EC2 instance based on the image
"Amazon Linux 2 AMI (HVM), SSD Volume Type - ami-0e8c04af2729ff1bb (64-bit x86)"
Ssh to the VM.


## Update packages, install git and download demo application
``` bash
sudo yum update -y
sudo yum install -y git
git clone https://github.com/progmaticlab/skynet
```

## If needed enable data SlackBot Application
``` bash
export SLACK_CHANNEL=skynet
export SHADOWCAT_BOT_TOKEN="xoxb-******"
```
Slack Bot App is to be created in advance, the app is to be added into the Slack channel 
and granted requried permisions.
Permissions required for the app: calls:read, calls:write, chat:write, files:write.
For backward notifications by buttons it is needed to manually set Request URL
for the app on Slack site: http://54.214.233.135:8080/slack/proxy/
It is because there is no way to do it dynamically.


## Deploy Amazon EKS cluster
For the first run you will be asked to provide your Amazon access key and
secret key as the applicaion needs to control Amazon EKS.
Your IAM account needs to have rights to control Amazon EKS.
``` bash
# provide name of cluster and context
export CLUSTER_NAME='istio-demo-cluster'
export CONTEXT_NAME='istio-demo'
./skynet/deploy_eks_sandbox.sh
```


## Run demo application
``` bash
./skynet/demo_inside_eks_sandbox.sh
```

## Demo workflow
- Start loading
- Wait for a few seconds
- Start collecting
- Wait till collecting gather about 30+ smaples
- Stop lerining mode
- Start stress reviews-v1
- Wait till anomalies be detected (it might take some time - usually ~1min+)
- Go to slack channel provided in options and check graphs
- Response with 'Describe Suggested RunBook' => check description in slack channel
- Response with 'Use Suggested RunBook' => go demo application, check that 
problematic pod is replaced and anomalies are gone
