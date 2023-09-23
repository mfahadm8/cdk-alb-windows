# CDK template application

This repository hosts a CDK project that provison App-Sp16 ECS cluster with autoscaling setup and fargate.

## Prerequisite:
Make sure you have the following installed and configured
- aws cli
- python3
- make 

## Presetup
1. Install `node.js version 16`, I noticed that aws cdk does not work well with some of the latest versions. Just to be safe, use the exact same versions

```bash
curl https://raw.githubusercontent.com/creationix/nvm/master/install.sh | bash
source ~/.bashrc
exec bash --login
```
- ### NVM validation
```bash
nvm --help
```
In case the command is not working, refresh the window/terminal or open up a new one.

2. ## Install nodejs 16 using nvm
```bash
nvm install 16
nvm use 16
```
- In case you are using windows or mac, you can install nodejs 16 from the following [Node Js 16 link](https://nodejs.org/en/download/current)

3. ## Install CDK
```bash
npm install -g aws-cdk
```

4. ## Setup Virtual Env and install dependencies
```bash
make local-venv
source .venv/bin/activate
make install-dependencies
```

5. ## CDK bootstrapping
If this is the first time you are using cdk in a particular region, you will have to bootstrap that region in order to deploy your stacks
```bash
make bootstrapp-cdk-toolkit
```

6. ## Create a python virualenv and install dependencies
Create python environment and install python dependencies
```bash
make local-venv
source .venv/bin/activate
make install-dependencies
```

That's it! Now you are ready to provision your stacks.

6. ## Stacks Deployment
Please make sure to run `make synth` command in case you make any changes to infrastructure logic and add something new. Run both commands to deploy the stacks in order.
### Step 1:Network Stack Deployment
```bash
make synth
make deploy
```

It will prompt you to accept hte changes, type yes and enter
### Step 2:ComputeStack Deployment
```bash
make synth
make deploy STACK=ComputeStack
```