# Reproducible analysis protocol v1 (draft)

## Inputs and clocks

Require a bundle identity, session goal, completion condition, relative-nanosecond event log, and frame files. Preserve file hashes externally if comparing different runs. `source=synthetic` must stay synthetic in derived outputs. Missing goal or success evidence is an unresolved question, not permission to infer success from the last frame.

Events have stable `id`, `kind`, and `t_ns`. Frame timestamps are capture completion; `capture_start_ns` bounds the capture interval. Use timestamp order, then numeric event ID as a tie-breaker. File order can differ slightly from event time because capture and hook threads enqueue concurrently. Do not claim a frame represents the exact instant of an input occurring inside its capture interval.

Coordinates are physical client-area pixels. Each input/frame carries its contemporaneous `[screen_x, screen_y, width, height]`. Do not combine coordinates across resizing without converting them. Mark focus gaps, capture errors, partial sessions and missing frames as evidence gaps. Hooks record input received by Windows, not proof that the game accepted it.

## Candidate frames and state boundaries

For each button/key activation, select the latest frame whose capture finished before the input and the earliest frame whose capture began after it. Also inspect subsequent frames up to the next input. Keep the candidate event IDs in the draft. If no post-input frame exists, mark the outcome unresolved.

Group identical frame files first; visual near-duplicate merging is an explicit analyst decision in this version. Do not treat a cursor, decorative animation or clock change alone as a new semantic state. Split states when available actions, modal dialogs, selected tab/item, or task-relevant inventory/reward state differ. For a new state assign `s001`, `s002`, ... in order of first observation. If two visually identical screens have different task-relevant state, keep separate nodes and describe the distinguishing evidence. Do not fabricate hidden state.

A transient frame must not be silently discarded when it could affect input. Document suspected loading/animation and retain its evidence. Multiple inputs before the next stable frame form an ambiguous action sequence; do not arbitrarily attribute the outcome to the last click.

## Edges and hitboxes

Use `t001`, `t002`, ... in first-observed order. Each edge references pre-frame, input event(s), and post-frame. A click point is observed; a rectangle estimated from a screenshot is inferred. Set `certainty=inferred` and explain the estimated boundary in `uncertainty`, even when the transition itself was observed. Use `user_confirmed` only after explicit review, and record what was confirmed. Synthetic author-defined edges use `synthetic`.

The current replay format supports left-click rectangles only. Report keyboard, wheel, drag, timed or conditional transitions as unsupported rather than silently translating them into clicks. Multiple identical source/hitbox edges with different destinations need an explicit condition extension; the current player chooses the first matching edge and cannot evaluate these faithfully.

Only include recorded branches as observed. Separate any hypothetical cancel/retry/error branches in `assumptions` pending review. An unsupported/incomplete path must be reported as an incomplete draft even if structural validation passes.

## Output contract

`schema_version: 1`; `source: recorded|synthetic`; nonempty `goal`; `viewport: [width,height]`; local `events` path; `initial` node ID.

`nodes`: `{id, image, evidence: [event IDs]}`. Images and events must stay within the output directory. Optional `description` explains semantic distinctions.

`transitions`: `{id, from, to, rect: [x,y,width,height], certainty, evidence}` with optional `uncertainty`. Coordinates are in the declared viewport. Preserve any resizing ambiguity in assumptions; one fixed viewport cannot faithfully replay arbitrary layout changes.

`success`: `{node, description, evidence}`. This records the intended success assertion, not an independent proof. Confirmation comes from the user's stated task and visible outcome. `assumptions`: a list of unverified decisions and missing evidence.

## Reproducibility evaluation after real recordings exist

Give a fresh analyst only this skill, raw bundle and the goal/completion metadata. Do not supply previous drafts or conversational context. Compare state partitioning, observed edge endpoints, evidence IDs, success semantics and uncertainty coverage; wording need not match. Fix event/frame selection rules when disagreements are mechanical. Escalate ambiguous game semantics for user review. Record protocol version and decisions used. Structural validation is necessary but does not establish interpretation accuracy or real-game reliability.
