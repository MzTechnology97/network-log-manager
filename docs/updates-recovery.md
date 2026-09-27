# Updates and recovery

## Release channels

- `stable`: versioned production releases; prereleases excluded.
- `candidate`: release candidates.
- `development`: main branch for development/testing.

## Safe update sequence

Before changing application code, the updater creates a configuration/application-database backup and a schema-only backup of the large log database. It then applies immutable migrations, deploys the target application and runs health checks.

If application health fails, application code is returned to the previous revision. Database migrations are forward-only and are never automatically undone.

## Backups

The update backup intentionally does not duplicate the potentially huge connection-log dataset. Permanent historical logs are protected through the independent archive/retention strategy.

## Migration rules

Applied migration files must never be edited. `schema_migrations` stores the checksum of each applied migration; a checksum mismatch aborts the process.

## Recovery principle

Application releases, MariaDB data and permanent archives are separate lifecycle domains. Replacing or rolling back application code must not delete database volumes or archive storage.


## Archive-root permissions during Docker updates

Docker updates ensure that the configured archive root exists with runtime ownership UID 10001/GID 999 and mode 0750 before deployment. This repair is deliberately non-recursive: existing historical objects are not mass-reowned. Archive data remains outside the application release lifecycle.
