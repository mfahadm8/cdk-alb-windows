from typing import Dict

from aws_cdk import Stack, Fn, aws_ssm as ssm
from utils.stack_util import add_tags_to_stack
from .ec2 import Ec2
from constructs import Construct
from utils.ssm_util import SsmParameterFetcher
import boto3


class ComputeStack(Stack):
    def __init__(self, scope: Construct, id: str, config: Dict, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)
        ssm_client = boto3.client("ssm", config["aws_region"])
        # Apply common tags to stack resources.
        add_tags_to_stack(self, config)
        # create the ecs cluster
        vpc_id = None
        try:
            response = ssm_client.get_parameter(
                Name="/sp16/app/" + config["stage"] + "/vpc_id",
                WithDecryption=True,  # To decrypt secure string parameters
            )

            # Extract the value of the parameter
            vpc_id = response["Parameter"]["Value"]
        except:
            pass
        
        if vpc_id:
            self._ecs = Ec2(self, "Ecs", config, vpc_id)
