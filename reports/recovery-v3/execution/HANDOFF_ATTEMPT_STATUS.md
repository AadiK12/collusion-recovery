# Administrative handoff attempt

The exact identified dispatcher PID 88061 was SIGSTOP'd once. The readiness
utility rejected `unrecognized lock holder output`. The same PID/start identity
was immediately verified and SIGCONT'd unchanged. No process was terminated;
no journal was edited and no readiness proof was produced. Induction continues.

Native output is `p88061` followed by `f3`; macOS includes the file descriptor
field even with `-Fp`. A narrow additive observer override at
`experiments/recovery_v3/handoff_observer.py` accepts numeric descriptor records
only after a valid process record. It preserves all owner IDs and rejects all
other output. Parent-owned administrative utility is unchanged. Focused tests
and independent review precede another guarded attempt.

Separately the accounting reviewer found a real reservation event nesting issue
in runner progress, now fixed to `event["reservation"]["reservation_id"]`.
An offline full-wave integration test is being added using real-shaped events.
This changes the runner hash relative to the earlier science review; exact
release review will bind the corrected source.
