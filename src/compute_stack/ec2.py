from typing import Dict

from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_elasticloadbalancingv2 as elb2,
    aws_elasticloadbalancingv2_targets as targets,
    aws_ecs_patterns as ecs_patterns,
    aws_servicediscovery as servicediscovery,
    aws_elasticloadbalancingv2 as elbv2,
    aws_applicationautoscaling as appautoscaling,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    aws_ssm as ssm,
    aws_efs,
    aws_logs,
    aws_cloudwatch as cloudwatch,
    Duration,
    aws_autoscaling as autoscaling,
    aws_iam as iam,
    RemovalPolicy,
    Expiration,
)
from constructs import Construct
import base64
import json


class Ec2(Construct):
    _config: Dict
    _vpc = ec2.IVpc

    def __init__(self, scope: Construct, id: str, config: Dict, vpc_id: str) -> None:
        super().__init__(scope, id)
        self._config = config
        self._region = self._config["aws_region"]
        # Create cluster control plane
        self._vpc = ec2.Vpc.from_lookup(self, "Sp16Vpc", vpc_id=vpc_id)
        self.__create_windows_datacenter_instance("instance1")
        self.__create_windows_datacenter_instance("instance2")
        self.__create_windows_datacenter_instance("instance3")

        # self.__setup_application_load_balancer()
        # self.__setup_application_app_service_load_balancer_rule()
        # self.__setup_route53_domain()

    def __create_windows_datacenter_instance(self, namespace):
        ebs_devices = {
            "/dev/sda1",
            "/dev/sdb",
            "/dev/sdc",
        }
        instance_config = self._config["compute"]["ec2"][namespace]

        # Define the instance type, subnet, and security group
        instance_type = ec2.InstanceType.of(
            ec2.InstanceClass.M5, ec2.InstanceSize.XLARGE
        )

        ec2_security_group = ec2.SecurityGroup(
            self,
            f"{namespace}SecurityGroup",
            vpc=self._vpc,
            allow_all_outbound=True,
        )

        # Add inbound rules to the security group
        for port in instance_config["security_group"]["inbound"]:
            ec2_security_group.add_ingress_rule(
                peer=ec2.Peer.any_ipv4(),
                connection=ec2.Port.tcp(port),
            )

        block_devices = []
        # Create EBS volumes dynamically based on the values in the configuration
        for i, volume_size in enumerate(instance_config["ebs"], start=1):
            # Use "/dev/sda1" for the root volume and "/dev/xvd[f-z]" for additional volumes
            device_name = f"/dev/sda1" if i == 1 else f"/dev/xvd{chr(97+i)}"
            block_devices.append(
                ec2.BlockDevice(
                    device_name=device_name,
                    volume=ec2.BlockDeviceVolume.ebs(
                        volume_size=100,  # C: 100GB
                        volume_type=ec2.EbsDeviceVolumeType.GP2,
                    ),
                )
            )

        # Create the EC2 instance using configuration
        ec2_instance = ec2.Instance(
            self,
            namespace,
            instance_type=instance_type,
            machine_image=ec2.MachineImage.from_ssm_parameter(instance_config["ami"]),
            security_group=ec2_security_group,
            key_name=instance_config["keypair"],
            vpc=self._vpc,
            block_devices=block_devices,
            role=self.__create_ec2_role(namespace),
            instance_name=instance_config["name"],
            vpc_subnets=ec2.SubnetSelection(
                subnet_group_name=instance_config["subnet_name"]
            ),  # Replace with the appropriate selection logic
        )

    def __create_ec2_role(self, namespace) -> iam.Role:
        # Create IAM role for EC2 instances
        role = iam.Role(
            self,
            "ec2-role-" + namespace + self._config["stage"],
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        )
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "CloudWatchAgentServerPolicy"
            )
        )

        return role

    def __setup_application_load_balancer(self):
        # Create security group for the load balancer
        lb_security_group = ec2.SecurityGroup(
            self,
            "LoadBalancerSecurityGroup",
            vpc=self._vpc,
            allow_all_outbound=True,
        )
        lb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
        )

        # Create load balancer
        self.lb = elbv2.ApplicationLoadBalancer(
            self,
            "LoadBalancer",
            vpc=self._cluster.vpc,
            internet_facing=True,
            security_group=lb_security_group,
        )

    def __setup_application_app_service_load_balancer_rule(self):
        # Create target group
        app_target_group = elbv2.ApplicationTargetGroup(
            self,
            "TargetGroup-App",
            vpc=self._vpc,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[self._app_sp16_app_service],
            health_check=elbv2.HealthCheck(
                path="/v1/generation/health",
                protocol=elbv2.Protocol.HTTP,
                interval=Duration.seconds(60),
                timeout=Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=5,
            ),
        )

        # Create HTTP listener for redirection
        self.lb_http_listener = self.lb.add_listener(
            "HttpListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_target_groups=[app_target_group],
        )

    def __setup_route53_domain(self):
        # Add listener certificate (assuming you have a certificate in AWS Certificate Manager)
        self.lb_https_listener.add_certificates(
            "ListenerCertificate",
            certificates=[
                elbv2.ListenerCertificate.from_arn(self._config["domain"]["cert_arn"])
            ],
        )

        hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
            self,
            "hostedZone",
            hosted_zone_id=self._config["domain"]["hostedzone_id"],
            zone_name=self._config["domain"]["hostedzone_name"],
        )

        route53.ARecord(
            self,
            "ALBRecord",
            zone=hosted_zone,
            record_name=self._config["domain"]["domain_name"],
            target=route53.RecordTarget.from_alias(
                route53_targets.LoadBalancerTarget(self.lb)
            ),
        )
