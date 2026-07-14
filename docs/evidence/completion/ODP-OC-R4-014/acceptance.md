# ODP-OC-R4-014 Acceptance

| Acceptance criterion | Evidence | Status |
| --- | --- | --- |
| Fresh `origin/dev` worktrees receive the design source | Canonical ZIP, extracted payload, index, manifest, audit, and task pack are tracked on this branch | Pass |
| Source bytes are exact | ZIP and five extracted hashes match the pinned manifest | Pass |
| Fleet can inspect the real design | Extracted interactive HTML and all support assets are present | Pass |
| Visual work cannot rely only on prose | Archive README defines required sync, verify, open, screenshot, and review steps | Pass |
| Existing implementation is untouched | Staged path guard contains documentation/archive paths only | Pass |

Merge to `dev` is required before this delivery is considered durable for
independent Fleet worktrees.
