# Encryption Policy

Acme Cloudworks is a fictional company used for demonstration purposes.

## Data at rest
All customer data stored in Acme databases, object storage and backups is encrypted at rest with AES-256. Keys are managed in a cloud key management service and rotated every 12 months. Database volumes use transparent disk encryption.

## Data in transit
All external traffic uses TLS 1.2 or higher. Internal service-to-service traffic uses mutual TLS. Legacy protocols (SSLv3, TLS 1.0 and 1.1) are disabled.

## Key management
Encryption keys are never stored alongside the data they protect. Access to key material requires two-person approval and is logged. Customer-managed keys are available on the Enterprise plan.
