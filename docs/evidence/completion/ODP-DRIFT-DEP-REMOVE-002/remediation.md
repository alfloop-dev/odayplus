# NLTK dependency removal candidate

This candidate replaces the production drift evaluator with the native core
introduced in PR #1219, retaining the public `EvidentlyDriftMonitor` and
`EvidentlyDriftResult` APIs, prediction/cohort/policy validation, and report
metrics. Data/feature and prediction drift execute the native engine;
performance drift retains its existing implementation. Result and report
provenance identify `native_drift`.

The resolver removes Evidently, NLTK and their unused transitive dependencies
from `uv.lock`. NumPy, pandas, SciPy and scikit-learn remain real dependencies
of the monitoring capability. No audit suppression, waiver, threshold change,
package override or dependency omission is used.

## Evidence boundaries

`candidate-inputs.json` identifies the exact input commits and candidate file
hashes. `candidate-audit.json` and the raw pip-audit outputs describe a live
scan of the candidate's complete Python 3.12 environment using the unmodified
PR #1188 gate implementation. These are working-tree measurements, not proof
that a candidate commit is reviewed, merged or deployed. Final verification
must bind the same files and lock to the published candidate commit.

The historic waiver-enabled PASS at `6d82eb1` is not remediation evidence.
This document grants no risk exception, merge approval or deployment GO.

## Golden comparison

The baseline fixtures from PR #1218 and native-core reference from PR #1219
remain byte-for-byte unchanged. The candidate runs the production monitor
against those fixed inputs. Statistical values are compared exactly with the
same locked NumPy/pandas/SciPy/scikit-learn versions; thresholds, methods,
verdicts, columns and governed metadata are not normalized away.

The comparison translates only explicit provenance changes: the engine name,
implementation type prefix and deterministic metric IDs. The compatible
`evidently-<uuid4>` snapshot identifier format is retained; the actual engine
field always identifies the native implementation.

One error contract changes explicitly: an entirely untyped/all-missing dataset
previously raised `ZeroDivisionError`; the native core raises a descriptive
`NativeDriftError` (`ValueError`) because no denominator exists. The input
remains rejected. This difference requires independent review.

## SBOM and attribution

The current candidate SBOM is `docs/evidence/sbom.json`. Generation, validation,
attestation and the runtime release workflow use that same path. Historical
completion receipts retain their original bytes. NOTICE is regenerated from
the installed dependency trees. The native implementation was derived by
inspection and execution of Evidently 0.7.21 (Apache-2.0); historical reference
artifacts and their provenance remain in the prerequisite evidence directories.

The statistical routines are adapted from Evidently 0.7.21, including its
legacy statistical tests, text domain-classifier calculations and dataset
column inference. The original wheel's Apache-2.0 license is retained verbatim
in `EVIDENTLY-LICENSE.txt`. The native module identifies the upstream project
and these changes: local configuration and report types, direct numerical-stack
calls, deterministic metric identifiers and explicit native provenance. The
installed-package NOTICE remains generated from the actual dependency tree;
removing the distribution does not remove this source attribution.

## Reproducing the historical reference

Use a separate checkout at baseline commit
`3ef557769d670c73ccc0c36b7ba5cc90b6635200`, synchronize its unchanged lock with
`uv sync --frozen --python 3.12`, and run its baseline reproduction instructions.
For the core reference, use a separate checkout at
`804a00b01901d553ffba50c10eef1eb9fd14ee98` and its capture script. Those historical
environments contain vulnerable NLTK and are reference tools only. Never
install them into the candidate or regenerate goldens to accommodate candidate
differences. The candidate baseline suite runs without either removed package.
