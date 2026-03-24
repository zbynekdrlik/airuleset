### Security Basics

- **Never commit credentials, API keys, passwords, or tokens** to version control.
- Ensure `.gitignore` covers: `.env`, `*.env.local`, `TARGETS.md`, `credentials.json`, `*.pem`, `*.key`.
- Store secrets in environment variables or external files referenced by `.gitignore`.
- When referencing secrets in documentation, use placeholders (e.g., `YOUR_API_KEY`).
- CI secrets go in GitHub Secrets, never in workflow files.
- If you accidentally stage a secret, remove it from git history, not just the latest commit.
