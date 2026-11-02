---
paths:
  - "*.html"
  - "*.css"
  - "*.js"
---

### Web Frontend Rules

- Every CSS class referenced in UI components MUST be defined in the stylesheet. Missing CSS = invisible UI = broken feature.
- E2E tests must verify that UI components actually render visible content (text, buttons, forms), not just that the page loads without errors.
- Check for specific text content, element visibility, and interactive behavior in tests.
- Frontend changes require corresponding Playwright or E2E test updates.

## UI usage data drives UI layout (#1225, owner 12.10.2026 and 1.11.2026)

Controls added wherever there is room fill a UI with things used once a year. So:

1. **Every app with a UI logs UI usage.** Log the control, the view, a timestamp and the action kind (click, change, open). Use one delegated listener keyed on stable ids (e.g. `data-testid`), so a new control needs no extra code. Exclude test traffic with a marker the test harness sets. Keep a year, so yearly actions show up. Serve a summary: count per control, distinct days used, last use, use during live windows.
2. **A UI review or redesign starts from that data.** Give each control a frequency class and confirm it with the owner: every session goes to first glance, weekly one click away, monthly or yearly deeper, never is a deletion candidate. Re-sorting the current layout is not a redesign.
3. **A new tab, panel or prominent control states its frequency class in the PR**, and why it needs that place. A rarely used function goes deep by default.
4. **An app with a UI and no usage log gets a foundation ticket**, like the version-on-dashboard check.
