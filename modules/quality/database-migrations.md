### Database Migration Policy

**If a project has users or production data, ALWAYS use incremental migrations. NEVER edit or replace the initial migration on a production project.**

#### Pre-production (no real data)

- Editing the initial migration directly is acceptable
- Dropping and recreating the database is acceptable
- Schema changes can be destructive

#### Production (has real users/data)

- **ALWAYS add incremental migrations** (`ALTER TABLE ADD COLUMN`, `CREATE TABLE IF NOT EXISTS`)
- **NEVER edit existing migrations** — they have already run on production databases
- **NEVER drop or recreate tables** that contain user data
- Incremental migrations must be idempotent (check if column/table exists before adding)
- Register new migrations in the migration runner
- The server should auto-migrate on startup

#### How to decide

If the project is deployed to any machine with real data (not just CI test databases), it is a production project. Use incremental migrations.

**When in doubt, use incremental migrations.** The cost of an unnecessary migration file is zero. The cost of losing production data is catastrophic.
