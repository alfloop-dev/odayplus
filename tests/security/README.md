# Security Tests

RBAC, ABAC, PII masking, export permission, and vulnerability tests.

`test_security_acceptance_suite.py` registers the production security controls
that block release when missing: authn/authz denial, export controls, PII
masking, high-risk dual approval, OWASP API cases, and CI security scans.
