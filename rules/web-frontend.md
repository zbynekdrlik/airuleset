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
