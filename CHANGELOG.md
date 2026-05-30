# Changelog

## [0.1.0] - 2026-05-20

Initial public release.

- Receives S3-PutObject events through SNS.
- Downloads and decompresses gzipped CloudFront access logs from S3.
- Parses the 33-column CloudFront tab-separated format with second-precision UTC timestamps (`date` + `time` columns combined).
- Converts records to nginx-style fields for easier dashboarding, plus the CloudFront `x-edge-request-id` as `request_id`.
- Bulk-indexes into a daily OpenSearch index keyed off the log file's own date (`<INDEX_PREFIX>-YYYY-MM-DD`), so backfills and Lambda retries land in the right index.
- Document `_id` uses `x-edge-request-id` for idempotent indexing (a retried Lambda run overwrites the same docs instead of creating duplicates). Falls back to OpenSearch auto-id when the field is empty.
- `OPENSEARCH_ENDPOINT` is a required environment variable; missing it raises `KeyError` during cold start.
