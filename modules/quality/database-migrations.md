### Database Migration Policy → auto-loads full detail on migration/`.sql` files (`rules/database-migrations.md`)

If a project has users or production data, ALWAYS use incremental migrations. NEVER edit or replace a migration that has already run on production, and NEVER drop/recreate a table containing user data. Pre-production (no real data): editing the initial migration directly is fine. When in doubt, use an incremental migration — its cost is zero next to losing production data.
