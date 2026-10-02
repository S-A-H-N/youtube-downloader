import os
import boto3

bucket = os.environ["B2_BUCKET_NAME"]
region = os.environ["B2_REGION"]

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://s3.{region}.backblazeb2.com",
    aws_access_key_id=os.environ["B2_KEY_ID"],
    aws_secret_access_key=os.environ["B2_APPLICATION_KEY"],
    region_name=region,
)

response = s3.list_objects_v2(
    Bucket=bucket,
    MaxKeys=1
)

print({
    "success": True,
    "bucket": bucket,
    "objects_visible": response.get("KeyCount", 0)
})
