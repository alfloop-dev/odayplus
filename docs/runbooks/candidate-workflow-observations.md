# Candidate workflow observations

The Candidate workflow displays four independent authorized observations, in
this order: data readiness, score job, saved score report, and the decision for
that exact report. This is presentation order, not a domain transition or an
execution permission. A later observation never clears an earlier failure,
denial, conflict, or unknown value.

Data readiness comes from the existing six-field Gate and its recorded field
evidence. It does not grant source, PIT, model, or execution admission. The
current admission observations remain separate; the command owner revalidates
all required conditions when accepting a command.

The job read covers the authorized candidate collection. Its original job id
is discovered only from verified current source-parent metadata. The existing
job endpoint still checks its own permission and parent scope. An initial job
does not require a completed report. When an original job succeeded and a
report exists, the existing execution context restores an active fresh request
or the latest completed predecessor. Its tenant, candidate, original job and
report/version must agree. Denied or unavailable context never becomes a
successful original-job fallback. Original outcome and delivery state remain
separate, as returned by their owner. No inference input or delivery token is
added to the UI.

A saved report is identified by candidate, report id/version, run and model.
It remains readable when a current job fails. A successful job without a report
in the current read is shown as inconsistent until a refreshed read resolves
it; the client does not create a report, infer a numeric score, or retry a job.
The SiteScore step's completed marker means a saved report is available, not
that the current job succeeded. Without a report, a queued/running job is the
current step and a failed job remains blocked.

The decision joins the selected candidate and exact saved report/version. A
prior decision stays historical when a new report exists. Native lifecycle,
Decision, Approval, conditions and audit remain the persisted owners. Existing
human review projections retain their priority in the exclusive lifecycle
view; the scoring filter independently observes actual pending jobs, so an
approved candidate may also have a current scoring job. Missing observations
produce an unknown count, never zero. Shortlist remains unavailable until its
own producer exists; comparison membership is not shortlist membership.

Reads are discarded on source, access generation or location changes. Pending
jobs are polled through authorized GETs; a terminal transition refreshes the
report read once per observed job version. These observations do not grant
production model/source admission, Product approval, UAT or release approval.
