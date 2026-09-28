# Planning request library: first implementation slice

This feature is an additive staging implementation. It does not change the
published Fillout form, its existing Notion integration, or existing proposal
builder entry path. The latest supplied 2026 Fillout export is unpublished;
**Salesperson Details is the intended first page**. Its question IDs are the
source of the field map in `app/request_workflow.py`.

## Staging setup

1. Apply `schema.sql` to a fresh database if needed, then apply
   `migrations/001_request_workflow.sql` to a **staging** PostgreSQL database.
   The migration is additive and can be rerun. For an existing database,
   apply only the migration.
2. Set `REQUEST_WORKFLOW_ENABLED=1`, `FILLOUT_FORM_ID=<staging form id>`, and
   `FILLOUT_WEBHOOK_SECRET=<long random secret>` on the staging app. Keep the
   feature flag unset in production until the migration and intake have been
   validated.
3. Sign in with a planner account and open `/requests`. Current app admins
   act as team leads. A lead assigns an active user and sets priority; the
   owner moves a request from New to Progress before opening it in the
   proposal builder.
4. Configure a staging Fillout Advanced Webhook with a `POST` JSON body and
   custom header `X-Fillout-Webhook-Secret: <same secret>`. Use the versioned
   envelope below. The form's official [webhook guide](https://www.fillout.com/help/webhook)
   documents custom body fields and headers. Capture an actual staging test
   delivery before connecting the published form: the export contains form
   design, not a verified outbound webhook payload.

```json
{
  "schema_version": 1,
  "form_id": "staging-form-id",
  "submission_id": "fillout-submission-id",
  "answers": {
    "bWXCG9rN4vgJy3NW8PGXqi": "Taylor",
    "x1hGWHZCvQNBN1M4Mp3CH5": "Example",
    "dNY5mW453Jki7XaasbZjeq": "taylor@example.com",
    "dmv47zNJ7ZuPHCoohkNYn6": "El Centro-Yuma (Tier 2)",
    "q29Yauj9AFxZxg2nWGBEJh": "Proposal Page With Avails",
    "mtLKYCYxL7ZhAGp4eg2G3d": "Example Client",
    "mGb32w7djAxC61VPY73kaR": 2000,
    "2RLiGygkKv.cy8qB3HbCHVBDH1BtCbjK1": "2026-10-01"
  }
}
```

The adapter accepts either a bare widget ID or `page.widget` key. Include
every visible submitted answer, including file URLs, in `answers`; unknown
IDs remain available in the request detail panel. Confirm Fillout's values
for choices, dates, files, and signed-in identity against a real staging
submission. The app does not assume the email answer alone proves Google
identity. The `form_id` allowlist and secret authenticate the sender; later
identity verification needs a proven signed-in identity field from Fillout.

## Record and workflow rules

- Each accepted source submission gets one request with a UUID primary key
  and a visible `REQ-000001` style staging code. This namespace stays
  separate from production Notion `EVC-*` IDs during parallel operation.
- Retries of the same submission and payload return the existing request.
  Different payloads for the same submission, missing required seller
  details, unknown request types, and malformed dates or budgets go into
  `planning_intake_events` as **quarantined**. Admins can inspect the last
  100 issues at `GET /api/admin/request-intake`; no changed submission
  silently overwrites the original request.
- Original source details and identity columns cannot be edited through the
  API, and a database trigger blocks changes and deletes. Operational
  changes use a version check and create an activity entry in the same
  transaction. The activity table blocks updates and deletes.
- `Reviewing` means the plan has been delivered to the seller and awaits
  feedback. The transition requires a plan URL or a delivery note. The owner may move it back
  to `Progress` for revisions. Only a lead may mark `Done` or `Canceled`;
  cancellation requires a reason. `Done` and `Canceled` are terminal in
  this first slice.
- The request detail starts the existing proposal builder only in Progress
  and only for the owner or a lead. The builder pre-fills Step 02, where the
  planner checks source values. Saving a draft or generating a proposal
  creates a permanent link to the request. Linked drafts cannot be deleted.
  A suspicious budget such as the supplied value `7` stays unchanged and
  is flagged for confirmation. Fillout product categories are not assumed to
  be exact catalog SKUs; unmatched choices stay visible with a planner warning
  until the planner selects the correct catalog products.

## Before a production cutover

This branch is a foundation, not a Notion replacement yet. Validate the
staging webhook envelope end to end and reconcile its answers against each
of the seven Fillout request branches. Add a reviewed correction mechanism,
research subtasks, comments and notifications, SSO and scoped planning
roles, historical Notion import and ID crosswalk, attachment retention,
and reconciliation reporting before switching off Notion. The current
proposal builder still saves generated files locally; moving request
tracking into PostgreSQL does not by itself make those files durable on
Replit autoscale. Define shared object storage before depending on them as
the permanent request library.
