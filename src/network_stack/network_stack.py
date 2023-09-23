from typing import Dict

from aws_cdk import aws_ec2 as ec2, aws_ssm as ssm, Stack

from utils.stack_util import add_tags_to_stack
from .vpc import Vpc
from constructs import Construct


class NetworkStack(Stack):
    _vpc: ec2.IVpc
    config: Dict

    def __init__(self, scope: Construct, id: str, config: Dict, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)
        self.config = config
        # Apply common tags to stack resources.
        add_tags_to_stack(self, config)

        vpcConstruct = Vpc(self, "Vpc", config)
        self._vpc = vpcConstruct.vpc