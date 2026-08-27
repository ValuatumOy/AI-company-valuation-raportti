# Project rules — AI-company-valuation-raportti

## Never run report generations against prod without explicit approval

Do NOT call `POST /api/runs`, `POST /api/runs/{rid}/start`, or
`POST /api/runs/{rid}/round2` against the production backend
(`valu-pipeline-production-88f2.up.railway.app`) — or trigger a run through
the admin frontend / client site pointed at prod — without asking the user
first and getting an explicit go-ahead, even for "just a quick verification
run."

**Why:** each single-writer run costs real money (~$3/round-1, ~$0.6/round-2)
and there is no cancel endpoint — `DELETE /api/runs/{rid}` refuses while a
run is still executing, so once started it always runs to completion and
spends the money. This bit us during the 2026-07-05 cost incident (see
HANDOFF.md) and again on 2026-07-07 when a "let me verify this" run was
kicked off mid-session before the user had finished making changes.

**How to apply:** before starting or resuming ANY run against prod (round-1
create+start, round-2, or a scoped stage rerun on an existing prod run),
stop and ask the user to confirm. This applies even when a HANDOFF.md
"pick up here" section says a verification run is the next step — surface
the plan and wait for a yes before calling the API. Local/test-only actions
(reading `/api/health`, `/api/runs/{rid}` GET, `/api/companies`,
`/api/pipelines`, running the pytest suite) don't need approval.
