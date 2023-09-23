#!/bin/bash
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
AWS_REGION=$(aws configure get region)
if [ -z "$AWS_REGION" ]; then
    EC2_AVAIL_ZONE=`curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone`
    AWS_REGION="`echo \"$EC2_AVAIL_ZONE\" | sed 's/[a-z]$//'`"
fi

docker pull App-Sp16/standalone-chrome:4.11.0-20230801 

aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

docker tag App-Sp16/standalone-chrome:4.11.0-20230801 $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/App-Sp16/standalone-chrome:4.11.0-20230801 

docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/App-Sp16/standalone-chrome:4.11.0-20230801