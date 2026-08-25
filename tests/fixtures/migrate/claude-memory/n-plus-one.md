---
type: mistake
created: 2026-04-02
author: claude-code
---

# N+1 query on the invoice list

Each invoice re-fetched its line items.
