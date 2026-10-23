# Store Ops workflow confirmation and bounded retry

Date: 2026-10-12

## Confirmed defect

All nine Store Ops forms previously invoked callbacks and dismissed their dialog
before the POST completed. HTTP failures and transport loss reached only a generic
refresh event, leaving no in-form acknowledgement. Each attempt generated a new
idempotency key, so an uncertain result could be submitted as another command.

## Current behavior

Triage, assign, create action, field report, outcome, escalate, camera purpose,
reply review and transfer now share the same submission lifecycle. Existing payloads,
validation, endpoint routes, actor verification and backend scope rules are retained.

- The modal owns its focus boundary. Initial focus enters the first editable field.
- While the POST is pending, its fieldset and close button are disabled. Escape,
  cancel and duplicate form submission cannot dismiss or resend the request.
  Focus moves to the visible pending status. Focus discovery respects disabled
  fieldset descendants through the native `:disabled` selector.
- A successful HTTP response must contain `issue.id` matching the submitted issue.
  Only then are callbacks invoked, the existing refresh event emitted and the
  dialog dismissed.
- Non-success HTTP responses, malformed or mismatched acknowledgements and
  transport loss keep the form and its inputs visible. The inline alert receives
  focus. Server details and arbitrary transport messages are not displayed.
- A 20-second client timeout ends waiting and reports an uncertain result. Aborting
  the client request does not imply the server rolled back.
- An unchanged payload and selected role reuse the same idempotency key within the
  open dialog, including retries after timeout or lost acknowledgement. Edited
  intent uses a new key. Each attempt has a separate correlation identifier.

## Limits and remaining acceptance

This is a bounded in-memory retry mechanism, not durable cross-reload recovery or
an exactly-once guarantee. Closing a failed form, reloading or changing its issue
ends that local intent. Server-side actor/request/scope checks remain authoritative.
No new API schema, source policy or business/audit/outbox transaction was introduced.

The nine workflows still require independent exact-source and design review. Local
fixture browser tests prove UI behavior, geometry and keyboard accessibility; they
are not real-account UAT or deployed production readback. The supplemental tests
remain outside the fixed business-acceptance inventory. Release authority, live
staging/DR/cutover and the existing manual UAT requirements remain pending.

## Verification

The evidence packet records the exact candidate commit and raw results. The focused
component suite covers all nine pending forms, validation, callback timing, failed
and uncertain retries, edited intent and mismatched issue acknowledgement. The full
web suite exercises the shared modal behavior across other workspaces. Browser
checks use 1440/1024/390 widths and the archived Package 10 original; pending/error
states are checked with axe and actual keyboard navigation. Synthetic requests do
not authorize real-account actions or production changes.
