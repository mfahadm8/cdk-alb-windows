from typing import Dict, List

from aws_cdk import aws_ec2 as ec2, aws_ssm as ssm
from constructs import Construct


class Vpc(Construct):
    config: Dict
    vpc: ec2.Vpc
    subnet_configuration: List[ec2.SubnetConfiguration] = []

    def __init__(self, scope: Construct, id: str, config: Dict) -> None:
        super().__init__(scope, id)
        self.config = config
        self.__create_vpc()

    def __create_vpc(self):
        # Configuration
        vpc_config = self.config["network"]["vpc"]
        private_subnets_config = self.config["network"]["subnets"]["private"]
        public_subnets_config = self.config["network"]["subnets"]["public"]

        print(private_subnets_config)
        print(public_subnets_config)
        # Create VPC
        # Create VPC resource
        self.vpc = ec2.CfnVPC(
            self,
            "AppSp6VpcCfn",
            cidr_block=self.config["network"]["vpc"]["cidr"],
            enable_dns_support=True,
            enable_dns_hostnames=True,
            tags=[{"key": "Name", "value": "AppSp6Vpc"}],
        )

        # Create Internet Gateway
        internet_gateway = ec2.CfnInternetGateway(
            self,
            "InternetGateway",
        )

        # Attach Internet Gateway to VPC
        ec2.CfnVPCGatewayAttachment(
            self,
            "GatewayToInternet",
            vpc_id=self.vpc.ref,
            internet_gateway_id=internet_gateway.ref,
        )

        # Create one Private and one Public Route Table
        private_route_table = ec2.CfnRouteTable(
            self,
            "PrivateRouteTable",
            vpc_id=self.vpc.ref,
        )

        public_route_table = ec2.CfnRouteTable(
            self,
            "PublicRouteTable",
            vpc_id=self.vpc.ref,
        )

        nat_gateway_subnet = None
        nat_gateway_eip = ec2.CfnEIP(self, "NATGatewayEIP")

        # Add default route to Internet Gateway in the Public Route Table
        ec2.CfnRoute(
            self,
            f"PrivateRouteIGW",
            route_table_id=public_route_table.ref,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=internet_gateway.ref,
        )

        # Iterate over public subnets configuration
        for idx, subnet_config in enumerate(public_subnets_config):
            public_subnet = ec2.CfnSubnet(
                self,
                f"PublicSubnet{idx}",
                vpc_id=self.vpc.ref,
                availability_zone=subnet_config["avl_zone"],
                cidr_block=subnet_config["cidr_block"],
                tags=[{"key": "Name", "value": f"PublicSubnet{idx}"}],
            )

            # Associate Public Subnets with the Public Route Table
            ec2.CfnSubnetRouteTableAssociation(
                self,
                f"PublicSubnet{idx}RouteTableAssociation",
                subnet_id=public_subnet.ref,
                route_table_id=public_route_table.ref,
            )

            if idx == 0:
                nat_gateway_subnet = public_subnet

        # Create NAT Gateway in the first public subnet with the allocated EIP
        nat_gateway = ec2.CfnNatGateway(
            self,
            "NATGateway",
            subnet_id=nat_gateway_subnet.ref,
            allocation_id=nat_gateway_eip.attr_allocation_id,
        )

        # Add default route to NAT Gateway in the Private Route Table
        ec2.CfnRoute(
            self,
            f"PrivateRouteNatGW",
            route_table_id=private_route_table.ref,
            destination_cidr_block="0.0.0.0/0",
            nat_gateway_id=nat_gateway.ref,
        )

        # Iterate over private subnets configuration
        for idx, subnet_config in enumerate(private_subnets_config):
            private_subnet = ec2.CfnSubnet(
                self,
                f"PrivateSubnet{idx}",
                vpc_id=self.vpc.ref,
                availability_zone=subnet_config["avl_zone"],
                cidr_block=subnet_config["cidr_block"],
                tags=[{"key": "Name", "value": f"PrivateSubnet{idx}"}],
            )

            # Associate Private Subnets with the Private Route Table
            ec2.CfnSubnetRouteTableAssociation(
                self,
                f"PrivateSubnet{idx}RouteTableAssociation",
                subnet_id=private_subnet.ref,
                route_table_id=private_route_table.ref,
            )

        ssm.StringParameter(
            scope=self,
            id="vpcId",
            tier=ssm.ParameterTier.STANDARD,
            string_value=self.vpc.ref,
            parameter_name="/sp16/app/" + self.config["stage"] + "/vpc_id",
        )
