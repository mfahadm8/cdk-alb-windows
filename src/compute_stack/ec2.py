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
from utils.ssm_util import SsmParameterFetcher
import base64
import json


class Ec2(Construct):
    _config: Dict
    _cluster: ecs.ICluster
    _app_sp16_app_service: ecs.FargateService
    _vpc = ec2.IVpc

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Dict,
        vpc: ec2.Vpc,
        efs: aws_efs.FileSystem,
    ) -> None:
        super().__init__(scope, id)
        self._config = config
        self._region = self._config["aws_region"]
        # Create cluster control plane
        self._vpc = vpc
        self._efs = efs
        self.__create_app_sp16_training_service()
        self.__create_app_sp16_training2x_service()
        self.__create_app_sp16_app_service()
        self.__setup_application_load_balancer()
        self.__setup_application_training_service_load_balancer_rule()
        self.__setup_application_training2x_service_load_balancer_rule()
        self.__setup_application_app_service_load_balancer_rule()
        self.__setup_route53_domain()

    def __get_ec2_autoscaling_group(
        self,
        namespace,
        instance_type=ec2.InstanceType.of(
            ec2.InstanceClass.G4DN, ec2.InstanceSize.XLARGE
        ),
    ):
        cloudwatch_agent_config = {
            "metrics": {
                "namespace": namespace,
                "metrics_collected": {
                    "nvidia_gpu": {
                        "measurement": [
                            {
                                "name": "memory_used",
                                "rename": "nvidia_smi_memory_used",
                                "unit": "Megabytes",
                            }
                        ],
                        "metrics_collection_interval": 60,
                    }
                },
                "aggregation_dimensions": [[]],
            }
        }
        cloudwatch_agent_config_json = json.dumps(cloudwatch_agent_config)

        user_data = ec2.UserData.for_linux(shebang="#!/usr/bin/bash")
        user_data_script = """#!/usr/bin/bash
        echo ECS_CLUSTER={} >> /etc/ecs/ecs.config
        sudo iptables --insert FORWARD 1 --in-interface docker+ --destination 169.254.169.254/32 --jump DROP
        sudo service iptables save
        echo ECS_AWSVPC_BLOCK_IMDS=true >> /etc/ecs/ecs.config
        echo ECS_ENABLE_GPU_SUPPORT=true >> /etc/ecs/ecs.config
        cat /etc/ecs/ecs.config
        sudo amazon-linux-extras install -y amazon-ssm-agent
        sudo systemctl start amazon-ssm-agent
        sudo systemctl enable amazon-ssm-agent
        sudo yum install -y amazon-cloudwatch-agent
        echo '{}' > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
        /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
        /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a start
        """.format(
            self.cluster_name, cloudwatch_agent_config_json
        )

        user_data.add_commands(user_data_script)

        ec2_security_group = ec2.SecurityGroup(
            self,
            "Ec2BalancerSecurityGroup-" + namespace,
            vpc=self._cluster.vpc,
            allow_all_outbound=True,
        )
        ec2_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.all_tcp(),
        )

        asg = autoscaling.AutoScalingGroup(
            self,
            "ECSEC2Capacity-" + namespace,
            vpc=self._vpc,
            min_capacity=self._config["compute"]["ecs"]["app"]["minimum_containers"],
            desired_capacity=self._config["compute"]["ecs"]["app"][
                "minimum_containers"
            ],
            max_capacity=self._config["compute"]["ecs"]["app"]["maximum_containers"],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC, one_per_az=True
            ),
            instance_type=instance_type,
            machine_image=ec2.MachineImage.generic_linux(
                ami_map={
                    self._region: self._config["compute"]["ecs"]["app"]["amis"][
                        self._region
                    ]
                }
            ),
            security_group=ec2_security_group,
            associate_public_ip_address=True,
            role=self.__create_ec2_role(namespace=namespace),
            key_name=self._config["compute"]["ecs"]["app"]["ec2_keypair"],
            user_data=user_data,
            new_instances_protected_from_scale_in=False,
            block_devices=[
                # Add the desired root volume size to the block device mappings
                autoscaling.BlockDevice(
                    device_name="/dev/xvda",
                    volume=autoscaling.BlockDeviceVolume.ebs(
                        volume_size=100,
                        volume_type=autoscaling.EbsDeviceVolumeType.GP2,
                    ),
                )
            ],
        )

        return asg

    def __create_app_sp16_training_service(self):
        namespace = "ls_training"
        asg = self.__get_ec2_autoscaling_group(namespace=namespace)
        # Create EC2 service for ui
        capacity_provider = ecs.AsgCapacityProvider(
            self,
            "AsgCapacityProvider-training",
            auto_scaling_group=asg,
            enable_managed_termination_protection=False,
            enable_managed_scaling=True,
        )
        self._cluster.add_asg_capacity_provider(capacity_provider)

        self._app_sp16_training_service = ecs.Ec2Service(
            self,
            "app_sp16training-service",
            cluster=self._cluster,
            task_definition=self.training_taskdef,
            desired_count=1,
            placement_constraints=[ecs.PlacementConstraint.distinct_instances()],
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider=capacity_provider.capacity_provider_name,
                    base=1,
                    weight=1,
                )
            ],
            health_check_grace_period=Duration.minutes(5),
        )

        self.__configure_service_autoscaling_rule(
            namespace, self._app_sp16_training_service
        )

    def __create_app_sp16_training2x_service(self):
        namespace = "ls_training2x"
        asg = self.__get_ec2_autoscaling_group(
            namespace=namespace,
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.G5, ec2.InstanceSize.XLARGE2
            ),
        )
        # Create EC2 service for ui
        capacity_provider = ecs.AsgCapacityProvider(
            self,
            "AsgCapacityProvider-training2x",
            auto_scaling_group=asg,
            enable_managed_termination_protection=False,
            enable_managed_scaling=True,
        )
        self._cluster.add_asg_capacity_provider(capacity_provider)

        self._app_sp16_training2x_service = ecs.Ec2Service(
            self,
            "app_sp16training2x-service",
            cluster=self._cluster,
            task_definition=self.training2x_taskdef,
            desired_count=1,
            placement_constraints=[ecs.PlacementConstraint.distinct_instances()],
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider=capacity_provider.capacity_provider_name,
                    base=1,
                    weight=1,
                )
            ],
            health_check_grace_period=Duration.minutes(5),
        )

        self.__configure_service_autoscaling_rule(
            namespace, self._app_sp16_training2x_service
        )

    def __create_app_sp16_app_service(self):
        namespace = "ls_app"
        asg = self.__get_ec2_autoscaling_group(namespace=namespace)
        capacity_provider = ecs.AsgCapacityProvider(
            self,
            "AsgCapacityProvider-app",
            auto_scaling_group=asg,
            enable_managed_termination_protection=False,
            enable_managed_scaling=True,
        )
        self._cluster.add_asg_capacity_provider(capacity_provider)

        self._app_sp16_app_service = ecs.Ec2Service(
            self,
            "app_sp16app-service",
            service_name="app_sp16app-service" + self._config["stage"],
            cluster=self._cluster,
            task_definition=self.app_taskdef,
            desired_count=1,
            placement_constraints=[ecs.PlacementConstraint.distinct_instances()],
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider=capacity_provider.capacity_provider_name,
                    base=1,
                    weight=1,
                )
            ],
            health_check_grace_period=Duration.minutes(5),
        )
        self._app_sp16_app_service.connections.allow_from(
            self._app_sp16_training_service,
            ec2.Port.tcp(self._config["compute"]["ecs"]["app"]["port"]),
        )
        self._app_sp16_app_service.connections.allow_from(
            self._app_sp16_training2x_service,
            ec2.Port.tcp(self._config["compute"]["ecs"]["app"]["port"]),
        )
        self.__configure_service_autoscaling_rule(namespace, self._app_sp16_app_service)

    def __create_ec2_role(self, namespace) -> iam.Role:
        # Create IAM role for EC2 instances
        role = iam.Role(
            self,
            "ec2-role-" + namespace + self._config["stage"],
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        )

        # Add necessary permissions to the EC2 role
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonECS_FullAccess")
        )
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AmazonEC2ContainerServiceforEC2Role"
            )
        )
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AmazonEC2RoleforSSM"
            )
        )
        role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "CloudWatchAgentServerPolicy"
            )
        )

        return role

    def __create_app_taskdef_role(self) -> iam.Role:
        # Create IAM role for task definition
        task_role = iam.Role(
            self,
            "app-task-role-" + self._config["stage"],
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # Attach S3 full access policy to the task role
        task_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess")
        )
        region = self._config["aws_region"]
        account = self._config["aws_account"]
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "elasticfilesystem:ClientRootAccess",
                    "elasticfilesystem:ClientWrite",
                    "elasticfilesystem:ClientMount",
                    "elasticfilesystem:DescribeMountTargets",
                ],
                resources=[
                    f"arn:aws:elasticfilesystem:{region}:{account}:file-system/{self._efs.file_system_id}"
                ],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ec2:DescribeAvailabilityZones"],
                resources=["*"],
            )
        )

        return task_role

    def __create_training_taskdef_role(self, role_prefix) -> iam.Role:
        # Create IAM role for task definition
        task_role = iam.Role(
            self,
            f"{role_prefix}-task-role-" + self._config["stage"],
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # Attach S3 full access policy to the task role
        task_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess")
        )

        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ssm:GetParameter",
                    "ssm:GetParameters",
                    "kms:Decrypt",
                    "kms:GenerateDataKey",
                ],
                resources=["*"],
            )
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ec2:DescribeAvailabilityZones"],
                resources=["*"],
            )
        )

        return task_role

    def __setup_application_load_balancer(self):
        # Create security group for the load balancer
        lb_security_group = ec2.SecurityGroup(
            self,
            "LoadBalancerSecurityGroup",
            vpc=self._cluster.vpc,
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
            vpc=self._cluster.vpc,
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

        # Create the listener rule
        rule = elbv2.CfnListenerRule(
            self,
            "ListenerRule",
            listener_arn=self.lb_https_listener.listener_arn,
            priority=50,
            actions=[
                elbv2.CfnListenerRule.ActionProperty(
                    type="forward",
                    target_group_arn=app_target_group.target_group_arn,
                )
            ],
            conditions=[
                elbv2.CfnListenerRule.RuleConditionProperty(
                    field="path-pattern",
                    values=["/v1/generation/*"],
                )
            ],
        )

        rule.add_dependency(app_target_group.node.default_child)

        # Create HTTP listener for redirection
        # self.lb_http_listener = self.lb.add_listener(
        #     "HttpListener", port=80, protocol=elbv2.ApplicationProtocol.HTTP,
        #     default_target_groups=[app_target_group],
        #
        # )

    def __setup_application_training_service_load_balancer_rule(self):
        # Create target group
        training_target_group = elbv2.ApplicationTargetGroup(
            self,
            "TargetGroup-Training",
            vpc=self._cluster.vpc,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[self._app_sp16_training_service],
            health_check=elbv2.HealthCheck(
                path="/v1/training/health",
                protocol=elbv2.Protocol.HTTP,
                interval=Duration.seconds(60),
                timeout=Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=5,
            ),
        )

        # Create HTTP listener for redirection
        lb_http_listener = self.lb.add_listener(
            "HttpListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
        )

        lb_http_listener.add_action(
            "HttpRedirect",
            action=elbv2.ListenerAction.redirect(
                port="443",
                protocol="HTTPS",
                permanent=True,
            ),
        )

        # Create HTTPS listener
        self.lb_https_listener = self.lb.add_listener(
            "HttpsListener",
            port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            default_target_groups=[training_target_group],
        )

        # # Create the listener rule
        # rule = elbv2.CfnListenerRule(
        #     self,
        #     "ListenerRule",
        #     listener_arn=self.lb_http_listener.listener_arn,
        #     priority=1,
        #     actions=[
        #         elbv2.CfnListenerRule.ActionProperty(
        #             type="forward",
        #             target_group_arn =training_target_group.target_group_arn,
        #         )
        #     ],
        #     conditions=[
        #         elbv2.CfnListenerRule.RuleConditionProperty(
        #             field="path-pattern",
        #             values=["/v1/training/*"],
        #         )
        #     ],
        # )
        #
        # rule.add_dependency(training_target_group.node.default_child)

    def __setup_application_training2x_service_load_balancer_rule(self):
        # Create target group
        training2x_target_group = elbv2.ApplicationTargetGroup(
            self,
            "TargetGroup-Training2x",
            vpc=self._cluster.vpc,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[self._app_sp16_training2x_service],
            health_check=elbv2.HealthCheck(
                path="/v1/training/health",
                protocol=elbv2.Protocol.HTTP,
                interval=Duration.seconds(60),
                timeout=Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=5,
            ),
        )

        # Create the listener rule
        rule = elbv2.CfnListenerRule(
            self,
            "ListenerRuleTraining2x",
            listener_arn=self.lb_https_listener.listener_arn,
            priority=10,
            actions=[
                elbv2.CfnListenerRule.ActionProperty(
                    type="forward",
                    target_group_arn=training2x_target_group.target_group_arn,
                )
            ],
            conditions=[
                elbv2.CfnListenerRule.RuleConditionProperty(
                    field="path-pattern",
                    values=["/v1/training/sxl*"],
                )
            ],
        )

        rule.add_dependency(training2x_target_group.node.default_child)

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

    def __configure_service_autoscaling_rule(self, namespace, ecs_service):
        # Add GPU VRAM scaling based on the CloudWatch metrics
        gpu_vram_metric = cloudwatch.Metric(
            namespace=namespace,
            metric_name="nvidia_smi_memory_used",
            period=Duration.minutes(1),
            statistic="Average",
        )

        gpu_scaling = appautoscaling.ScalableTarget(
            self,
            "gpu-vram-scaling-" + namespace,
            service_namespace=appautoscaling.ServiceNamespace.ECS,
            resource_id=f"service/{self._cluster.cluster_name}/{ecs_service.service_name}",
            scalable_dimension="ecs:service:DesiredCount",
            min_capacity=self._config["compute"]["ecs"]["app"]["minimum_containers"],
            max_capacity=self._config["compute"]["ecs"]["app"]["maximum_containers"],
        )

        gpu_scaling.scale_on_metric(
            "ScaleToGPURAMUsage-" + namespace,
            metric=gpu_vram_metric,
            scaling_steps=[
                appautoscaling.ScalingInterval(change=-1, lower=0, upper=8000),
                appautoscaling.ScalingInterval(change=+1, lower=8000, upper=15000),
            ],
            evaluation_periods=2,
            cooldown=Duration.minutes(5),
            adjustment_type=appautoscaling.AdjustmentType.CHANGE_IN_CAPACITY,
        )
