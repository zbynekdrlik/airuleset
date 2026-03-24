### Autonomous Verification Directive

**You are responsible for verifying that your work actually works. Never ask the user to test, verify, take screenshots, or act as a tester.**

After CI deploys to a target machine:

1. **SSH or MCP to the target** and verify the app is running: check processes, hit health endpoints, verify API responses.
2. **Report only verified facts:** `VERIFIED: [what you actually confirmed]` or `FAILED: [what was wrong]`.
3. **Never use speculative language** — no "should work", "will probably", "might be". Only report what you observed.
4. If verification fails, investigate and fix immediately. A deploy is not done until you have confirmed the app is running on the target machine.
5. If you need more verification steps, take them without asking permission.

**A compiling program is not a working program. CI green is not deploy verified. You must confirm the deployed app is alive.**
