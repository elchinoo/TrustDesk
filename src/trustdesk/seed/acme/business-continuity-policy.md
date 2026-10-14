# Business Continuity and Disaster Recovery

## Backups
Production databases are backed up continuously with point-in-time recovery for 14 days. Daily snapshots are retained for 35 days and copied to a second region.

## Recovery objectives
Recovery time objective (RTO) is 4 hours and recovery point objective (RPO) is 5 minutes for the core platform. Restore procedures are tested every quarter.

## Availability
The platform runs across three availability zones. Planned maintenance is performed with rolling deployments and no customer-visible downtime.
