import json
import gzip
import os
import boto3
import logging
from urllib.parse import unquote
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')

OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]  # required, e.g. https://search-xxx.eu-central-1.es.amazonaws.com
INDEX_PREFIX = os.environ.get("INDEX_PREFIX", "cloudfront-logs")

def lambda_handler(event, context):
    logger.info("Lambda function invoked")
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        sns_message = event['Records'][0]['Sns']['Message']
        s3_event = json.loads(sns_message)  

        bucket = s3_event['Records'][0]['s3']['bucket']['name']
        key = unquote(s3_event['Records'][0]['s3']['object']['key'])
        logger.info(f"Processing file: {key} from bucket: {bucket}")

        response = s3.get_object(Bucket=bucket, Key=key)
        logger.info("Successfully retrieved object from S3")

        gzipped_content = response['Body'].read()
        log_content = gzip.decompress(gzipped_content).decode('utf-8')
        logger.info("Log file decompressed and decoded")

        parsed_logs = parse_cloudfront_logs(log_content)
        logger.info(f"Parsed {len(parsed_logs)} log entries")

        send_to_opensearch(parsed_logs, key)
        logger.info("Logs sent to OpenSearch")

        return {
            'statusCode': 200,
            'body': json.dumps(f'Successfully processed {key}')
        }
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error processing file: {str(e)}')
        }

def parse_cloudfront_logs(log_content):
    logger.info("Parsing CloudFront logs")
    log_lines = [line for line in log_content.split('\n') if line and not line.startswith('#')]
    headers = [
        'date', 'time', 'x-edge-location', 'sc-bytes', 'c-ip', 'cs-method', 
        'cs(Host)', 'cs-uri-stem', 'sc-status', 'cs(Referer)', 'cs(User-Agent)', 
        'cs-uri-query', 'cs(Cookie)', 'x-edge-result-type', 'x-edge-request-id', 
        'x-host-header', 'cs-protocol', 'cs-bytes', 'time-taken', 'x-forwarded-for', 
        'ssl-protocol', 'ssl-cipher', 'x-edge-response-result-type', 
        'cs-protocol-version', 'fle-status', 'fle-encrypted-fields', 'c-port', 
        'time-to-first-byte', 'x-edge-detailed-result-type', 'sc-content-type', 
        'sc-content-len', 'sc-range-start', 'sc-range-end'
    ]
    parsed_logs = []
    for idx, line in enumerate(log_lines):
        fields = line.split('\t')
        log_dict = dict(zip(headers, fields))
        logger.debug(f"Parsing log line {idx+1}")
        nginx_log = convert_to_nginx_format(log_dict)
        parsed_logs.append(nginx_log)
    return parsed_logs

def convert_to_nginx_format(log_dict):
    # CloudFront stores date (YYYY-MM-DD) and time (HH:MM:SS UTC) as separate
    # tab-separated columns; combine for a second-precision timestamp.
    timestamp = f"{log_dict['date']}T{log_dict['time']}Z"
    user_agent = unquote(log_dict['cs(User-Agent)'])
    nginx_log = {
        '@timestamp': timestamp,
        'request_id': log_dict.get('x-edge-request-id', ''),
        'remote_addr': log_dict['c-ip'],
        'remote_user': '-',
        'request': f"{log_dict['cs-method']} {log_dict['cs-uri-stem']} {log_dict['cs-protocol-version']}",
        'status': log_dict['sc-status'],
        'body_bytes_sent': log_dict['sc-bytes'],
        'http_referer': log_dict['cs(Referer)'] if log_dict['cs(Referer)'] != '-' else '',
        'http_user_agent': user_agent,
        'host': log_dict['cs(Host)'],
        'request_time': float(log_dict['time-taken']),
        'upstream_response_time': '-',
        'upstream_status': '-'
    }
    logger.debug(f"Converted log to NGINX format: {nginx_log}")
    return nginx_log

def send_to_opensearch(parsed_logs, log_key):
    logger.info("Preparing to send logs to OpenSearch")
    if not parsed_logs:
        logger.info("No log entries to send")
        return

    # Index by the log file's own date (when the requests actually happened),
    # not by the Lambda execution time. Keeps backfills and retries in the
    # right index.
    log_date = parsed_logs[0]['@timestamp'][:10]
    index_name = f'{INDEX_PREFIX}-{log_date}'.lower()

    bulk_body = []
    for log in parsed_logs:
        # CloudFront's x-edge-request-id is unique per request, so using it as
        # the document _id makes the indexing idempotent: a retried Lambda run
        # writes over the same docs instead of duplicating them. If the field
        # is empty (rare, e.g. some Lambda@Edge log lines), let OpenSearch
        # assign an auto-id.
        request_id = log.get('request_id', '').strip()
        action = {'index': {'_index': index_name}}
        if request_id:
            action['index']['_id'] = request_id
        bulk_body.append(json.dumps(action))
        bulk_body.append(json.dumps(log))

    bulk_payload = '\n'.join(bulk_body) + '\n'

    try:
        http = urllib3.PoolManager()
        response = http.request(
            'POST',
            f"{OPENSEARCH_ENDPOINT}/_bulk",
            body=bulk_payload.encode('utf-8'),
            headers={'Content-Type': 'application/x-ndjson'}
        )
        logger.info(f"OpenSearch response status: {response.status}")
        logger.info(f"OpenSearch response data: {response.data}")
        if response.status != 200:
            logger.error(f"Error sending to OpenSearch: {response.data}")
    except Exception as e:
        logger.error(f"Exception sending to OpenSearch: {str(e)}")
