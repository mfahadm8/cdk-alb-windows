from typing import Dict

from aws_cdk import Stack, Fn, aws_ssm as ssm
from utils.stack_util import add_tags_to_stack
from .ec2 import Ec2
from constructs import Construct
from utils.ssm_util import get_ssm_param


class ComputeStack(Stack):
    def __init__(self, scope: Construct, id: str, config: Dict, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)
        # Apply common tags to stack resources.
        add_tags_to_stack(self, config)
        # create the ecs cluster
        vpc_id = get_ssm_param(
            param_name="/sp16/app/" + config["stage"] + "/vpc_id",
            region=config["aws_region"],
        )
        print(vpc_id)
        if vpc_id:
            self._ecs = Ec2(self, "Ecs", config, vpc_id)
