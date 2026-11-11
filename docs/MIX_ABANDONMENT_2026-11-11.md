# Abandon incomplete personal mixes

My mixes now offers Abandon mix on incomplete personal guided sessions, on desktop and mobile. The confirmation explains that the session leaves the active list, pours/notes remain, stock does not change, and it can be restored. An Abandoned filter contains Restore mix. Default history filters and dashboard resume exclude abandoned sessions.

The operation is owner-scoped and POST-only, including for administrators. It records only an abandoned_at timestamp in an immediate SQLite transaction. Repeated abandonment retains the first timestamp. Restore clears that timestamp. Completed/quick sessions and production-linked batches are rejected; production cancellation retains its existing job workflow. No completion timestamps, steps, measured pours, recovery data, inventory or recipe data are changed.

Abandoned sessions cannot be opened on the bench or mutated through normal step, notes, vessel, dynamic event or recovery entry points. Restore is required before continuing. Existing cleanup guards still retain unfinished-session data protection.

Startup migrations add nullable mix_sessions.abandoned_at idempotently. Deploy with tvw-update; no manual database command is required. No existing sessions are automatically abandoned.

Validation: five lifecycle regressions cover abandon/restore round-trip, unchanged session/steps and every other database table, history filters, dashboard resume exclusion, stale session rejection, owner isolation, production/completed/quick restrictions, both responsive control sets, migration and request idempotency. Broader API/lifecycle/community run: 147 checks, 146 passed; the pre-existing community-route redirect expectation fails. Compilation and whitespace checks pass. Live visual verification follows server deployment.
