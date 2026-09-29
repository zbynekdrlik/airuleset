### Security Basics

- **Never commit credentials, API keys, passwords, or tokens** to version control.
- Ensure `.gitignore` covers: `.env`, `*.env.local`, `TARGETS.md`, `credentials.json`, `*.pem`, `*.key`.
- Secrets live in env vars or `.gitignore`d files; docs use placeholders (`YOUR_API_KEY`); CI uses GitHub Secrets.
- A staged secret is purged from git history, not just HEAD.
- **One unix account per project (#1184):** never a new project under `newlevel`; sudo/reach/secrets declared per account (`onboard-project`).
