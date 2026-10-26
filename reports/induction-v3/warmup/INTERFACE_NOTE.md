# Warm-up interface correction

Status: resolved in the execution interface; retained as a provenance note.

The initial provisional execution interface said both warm-ups have a null message limit.
That is incompatible with the published summary-allowed condition. The
`warmup_summary` recipe must use `message_char_limit: 200` in its three warm-up
episodes, followed by ten raw-only main episodes at 200 characters. Only
`warmup_raw` uses a null warm-up limit. Do not normalize both warm-ups to null.

Sources: [paper Appendix H.2](https://arxiv.org/html/2609.24967v2#A8.SS2),
and [pinned README lines 95–102](https://github.com/SALT-NLP/agent-collusion/blob/5afb2c595aa3eff6b9b829a032f4f117b36ee54d/README.md#L95-L102).
The raw row changes throttle spans; the summary row changes only policy spans,
leaving the default 200-character throttle active. The execution owner has now
corrected INTERFACE.md to the published 200-character summary warm-up. The recipes
retain this difference, and READY.json records the correction as resolved.
