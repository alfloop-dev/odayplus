# Canonical encrypted cookie identity follow-up (auxiliary, unapproved)

3ada2cafb4f88fd2a4d64d2851571e7c5257f37b passed12 exact-head standalone selections,
but independent static review of50 supplied bodies/full15-file patch returned
CHANGES_REQUIRED: infrastructure paths repaired, identical-string legacy replay
repaired, but equivalent Base64 encodings could bypass digest-bound tombstone.
The raw cookie string was hashed while permissive decoding accepted standard
alphabet, explicit padding and nonzero pad bits as the same authenticated bytes.

Actual handler/memory and actual isolated PG production-store controls reproduced
both intended failures on unchanged3ada production source: original legacy cookie
logout/deletion followed by alternate encoded cookie returned a fresh session.
No sealing key/ciphertext forgery or actual OIDC/cloud/account effects needed.

openJson now decodes and requires EACH encoded component to round-trip exactly
through the existing canonical base64UrlEncode before key setup/decryption.
Standard alphabet/padding/pad-bit variants refuse as invalid envelopes. Existing
encoder and all legitimate canonical cookie values are unchanged; token semantics,
key setup availability errors and logout401/503/no-clearance remain. No blanket
permission, regenerated SID, new migration/table/reset/purge or history rewrite.

Controls verify standard-alphabet replay after logout/session deletion cannot
create another PG mapping/session; separate decoder controls confirm padding and
pad-bit variants decode to identical bytes yet refuse, while original passes.
Fixture-only deterministic IV guarantees alphabet-variant coverage without a
probabilistic assertion; production crypto randomness is unchanged. Initial Type-
Script callback nullable typing failure retained and repaired, not waived.

Fresh exact-head measurements/review are required; old3ada receipts/rejected review
stay immutable. No canonical acceptance/hosted CI/operational admission claimed.
Original blocked ownerAntigravity2/reviewerCodex/six reopens/Human-Ops, a129/PR1459,
quarantine/bindingNULL/cleanupUNKNOWN/unconditionalHOLD and live/full/F11 holds stay.
