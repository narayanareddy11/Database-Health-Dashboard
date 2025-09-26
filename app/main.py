
import os, boto3, json
#from shared.teams import post_to_teams, simple_card
from rds_enhanced.handler import run as run_rds_enhanced



def lambda_handler(event, context):
    env = {k: os.environ.get(k,"") for k in os.environ.keys()}
    region  = os.environ.get("AWS_REGION","us-east-1")
    webhook = os.environ["TEAMS_WEBHOOK"]
    session = boto3.Session(region_name=region)

    results = {}
    # flags: set to "true"/"false" to enable/disable modules
    enable_rds_enhanced = os.environ.get("ENABLE_RDS_ENHANCED", "true").lower() in ("1","true","yes","y")




    if enable_rds_enhanced:
        results["rds_enhanced"] = run_rds_enhanced(session, webhook, region, env)


    return {"ok": True, "modules": results}
