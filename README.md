# s3-event-parser

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) ![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white) ![AWS Lambda](https://img.shields.io/badge/AWS%20Lambda-FF9900?logo=awslambda&logoColor=white)

CloudFront drops gzipped access logs into S3 on its own schedule. This Lambda picks them up via SNS, parses the 33-column tab-separated format, and bulk-indexes the records into OpenSearch where you can actually graph them.

Full architecture write-up: [CloudFront Logs to OpenSearch Using AWS Lambda](https://furkangungor.medium.com/cloudfront-logs-to-opensearch-using-aws-lambda-c92d0b4916c8).

## How it works

S3 fires an ObjectCreated event when CloudFront writes a new log file. The notification goes through an SNS topic to this Lambda. The Lambda downloads the file, decompresses it, parses the tab-separated CloudFront format into Python dicts, reshapes each row into nginx-style fields (`@timestamp`, `remote_addr`, `request`, `status`, and friends) for easier Kibana/OpenSearch dashboards, and ships them to a daily index named `<INDEX_PREFIX>-YYYY-MM-DD` via the bulk API.

## Configuration

| Variable | Required | Default | Notes |
|---|---|---|---|
| `OPENSEARCH_ENDPOINT` | yes | (none) | Full HTTPS URL of the OpenSearch domain. The Lambda refuses to load without it. |
| `INDEX_PREFIX` | no | `cloudfront-logs` | Used to build the daily index name |

## Wiring

```
CloudFront standard logging → S3 (logs bucket)
                                  │
                                  ▼ ObjectCreated:Put event
                                  S3 → SNS topic
                                       │
                                       ▼ subscription
                                       Lambda (this code)
                                       │
                                       ▼ bulk POST
                                       OpenSearch
```

The S3 → SNS notification configuration on the bucket and the SNS → Lambda subscription are infrastructure prerequisites; they are not in this repo.

## Deploy

```bash
zip function.zip lambda_function.py
aws lambda update-function-code \
  --function-name s3-event-parser \
  --zip-file fileb://function.zip
```

Recommended Lambda settings:
- Runtime: `python3.12`
- Handler: `lambda_function.lambda_handler`
- Timeout: `120` seconds (varies with log file size)
- Memory: `512` MB
- VPC: same VPC + subnets as the OpenSearch domain if it is VPC-private

## Network assumption

The Lambda calls OpenSearch over plain HTTPS with no SigV4 signing. This works in one of two setups:

1. **VPC-private OpenSearch with permissive access policy.** The Lambda runs in the same VPC and the OpenSearch domain access policy allows requests from the Lambda's security group. This is what the code assumes by default.
2. **Public OpenSearch with IP-whitelisted access policy.** Less secure; not recommended.

If your domain requires SigV4 signing (fine-grained access control enabled), wrap the `urllib3` call in `requests_aws4auth` or use `opensearch-py` with the AWS auth helper. This is not built in.

## IAM

See `iam-policy.json`. Replace `<CLOUDFRONT_LOGS_BUCKET>` with the actual bucket name. The EC2 network-interface permissions are required only if the Lambda runs inside a VPC.

## What can bite you

The bulk POST is fire and forget. A non-200 response gets logged but nothing retries. For high-volume pipelines you want `tenacity` or hand-rolled backoff plus a dead-letter queue.

The entire decompressed log file lives in memory while the bulk payload is built. Lambda memory has to exceed the largest uncompressed log size. Bump it if OOM shows up in CloudWatch.

The script writes records but doesn't manage index templates or ISM policies. Define an index template in OpenSearch for proper field mapping, and an ISM policy to roll old indices to cold storage or delete them.

## Files

| File | What it is |
|---|---|
| `lambda_function.py` | Lambda handler + CloudFront log parser + OpenSearch bulk writer |
| `example.json` | Sanitized S3-via-SNS event for local testing |
| `iam-policy.json` | Lambda execution role policy |
| `requirements.txt` | Runtime deps (boto3, urllib3, both in Lambda runtime) |

## License

Released under MIT, full text in [LICENSE](LICENSE).
