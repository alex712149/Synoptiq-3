REAL_PROTOTYPE (13-day, 2025-02-26 -> 2025-03-10)

Archived out of the active artifacts/ path on promotion of REAL_12M
(2025-02-26 -> 2026-02-25, artifacts/real_12m/).

NOT DELETED — kept as a versioned fallback per explicit prior instruction
in this project's build history. backend/app/config.py already prefers
artifacts/real_12m automatically when it exists (see config.py line ~37),
so this move does not change production behavior; it only gets the
smaller set out of the way in the tree.

To reinstate as the active real artifact set (e.g. if REAL_12M is ever
found to be invalid), move artifacts_real_13day back to artifacts/real
and remove/rename artifacts/real_12m.
