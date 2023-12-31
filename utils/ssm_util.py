import boto3


def get_ssm_param(param_name, region, profile):
    session = boto3.Session(profile_name=profile)
    ssm_client = session.client("ssm", region)
    # create the ecs cluster
    param = None
    try:
        response = ssm_client.get_parameter(
            Name=param_name,
            WithDecryption=True,  # To decrypt secure string parameters
        )

        # Extract the value of the parameter
        param = response["Parameter"]["Value"]
    except Exception as e:
        print(e)

    return param
