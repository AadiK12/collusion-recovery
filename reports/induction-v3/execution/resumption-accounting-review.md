# Exact-release resumption accounting review

Status: **passed** for release `67f5ec29e9d76ed3e0b5e912b57dda221de3a0c3613171888c86fd6c59206b14`.

The release file `release/induction-v3/resumption.json` has SHA-256 `9a6ac5d9890b5c82612288412ddeed0cb54baada3e187514c80f70d488bc0d9c`. Its 1,275 source bindings and ten historical manifests validate, including history hash `8b7c58307c2eeff560272c7e7052fb3401c44e3fb3521561fd1c58f512ebfdd5`. The target run did not exist at review.

The prior V3 baseline is **468 physical attempts**, **$1.09956276 consumed**, and **$0.6402 reserved**. The frozen old HTTP 429 remains an unknown-cost liability. The new local capacity is **$98.26023724 and 3,532 attempts**, while aggregate caps remain **$100 V3 exposure**, **$250 lifetime exposure**, **4,000 V3 attempts**, and **three concurrent new calls**. Lifetime exposure is $49.14679936, leaving $200.85320064.

The constructor and every dispatch bind/recheck the original liability and historical inventory under the existing program lock. Local usage and local reservations stay separate from the external historical reserve. New unknown outcomes stop dispatch and remain reserved; the single frozen exception cannot absorb a later unknown call.

The three proofs cover **42 already-paid cached responses**, including **two previously uningested completed responses**. Cached replay does not create another reservation or settlement. The one explicitly authorized new call at the old 429 boundary receives a distinct physical identity and a new full reservation; the old reserve remains intact. Five unstarted dyads retain their original schedules.

Pacing precedes reservation. Mock integration tests confirm zero new attempts on pre-dispatch rejection/cancellation; retained reserves and no automatic retry for a new 429 or timeout; and preserved settled usage plus a stop when observation fails after a successful response. The approximate pacing estimate does not reduce the conservative spend reservation.

Validation passed: **39 original/new accounting tests**, plus **six integration tests rerun against the frozen release sources**. The JSON companion contains exact evidence hashes and check results. No actionable accounting finding remains.

This agent authored the additive ledger and independently reviewed dispatcher/pacer integration; separate source/security review supplies the independent implementation check. This review acquired no real program lock, accessed no credentials, made no provider request, and changed no old artifact. Launch must still pass the combined science/security/accounting gate and exact locked-history check.
