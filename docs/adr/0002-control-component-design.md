# ADR 0002: The control component and model creation

Date: 2026-08-17

## Status

Accepted

## Context

The board layer is shipped. The rest of the specification is the control component, with its seven responsibilities, and the creation of a model from six inputs: region declarations, agent declarations, seed register values, an admission rule, a termination predicate, and run budgets. Three questions cut across every increment: how a notification reaches an agent, how timed rules act while the system is idle, and how time stays testable. The decisions below govern issues 21 through 24; each was chosen against a named alternative.

## Decision

**Delivery is a callback in the agent declaration.** The control component invokes it on the thread that closed the batch window, with the control lock released: inline on the writer's thread for a zero window, on the clock's timer thread for a longer one. The library never runs, schedules, or kills agent work; a callback may run the whole agent cycle inline, so a test can run a multi-agent scenario without threads. Rejected: a polled queue, because the specification starts agents on notification; library-owned threads, because the library would then schedule agent work.

**One injected clock owns reading time and arming timers.** The `Clock` protocol carries `now()` and `call_at(when, call)`. `SystemClock`, the default, is the only code in the library that reads the operating system clock. `ManualClock.advance` moves time and fires due calls synchronously on the calling thread, in due order, ties in arming order, calls armed mid-advance included. The batch window, the acknowledgment deadline, and the wall-clock budget each arm one call when set, so an idle run still closes when a deadline passes. Rejected: a now-only clock with polling, because a poll period bounds latency and tests would wait; entry-evaluated deadlines checked on the next operation, because one dead agent and an idle application would then hold the run open forever, which is the outcome the specification's liveness step exists to prevent.

**Public time is timezone-aware UTC `datetime` for instants and `timedelta` for durations.** Rejected: monotonic float seconds, because the audit records wall-clock instants and two time scales would meet in one surface.

**The admission rule judges both write kinds**, through the union `ProposedWrite = ProposedContribution | ProposedPremiseWrite`. The specification runs the rule on every proposed write, and `set_premise` proposes a write the board sequences. Its verdict is `Accept` or `Reject` carrying the reason. Rejected: judging level writes only; a bare `None` standing for acceptance, which overloads absence with a verdict.

**Refusals return as values; defects raise.** `write` returns `Accepted | Rejected` and `set_premise` returns `Written | Conflict | Rejected`, because a refusal can race correct agent code. A kind mismatch raises `RegionKindError`, matching the board, because it is a caller defect. `RejectionCause` is a closed set defined from the first increment, because a member added later breaks an exhaustive match. Superseded on its membership by ADR 0016, which sets the axis: what the application's own configuration settles raises, and what the run's policy decides returns. Rejected: exceptions for refusals, because the specification returns the refusal to the writer.

**Writer identity is an explicit parameter, recorded, never validated against the registry.** The audit needs attribution and self-notification suppression needs the name; the specification lets any caller reach `set_premise` and treats every caller alike. Rejected: restricting writers to registered agents, which would force operators and scheduled jobs to register as notifiable agents.

**A notification carries its acknowledgment deadline, and notifications to one agent are not serialized.** A window closing while a prior notification is unacknowledged still fires; `ack` advances the cursor to the covered range's end, so overlap costs redundant reads, never corruption. Rejected: at most one outstanding notification per agent, a rule the specification lacks that would delay a premise change.

**A late acknowledgment is tolerated; a fabricated identifier raises.** An acknowledgment naming a notification issued to that agent but no longer outstanding changes nothing, and `extend` in that state returns no new deadline, because a deadline against real work is racy and an acknowledgment sent before the deadline can be processed after it. An identifier never issued to that agent raises, because it marks a defect in the caller. Rejected: raising on the race, which would force every well-behaved agent to guard its normal path; tolerating unknown identifiers, which hides bugs.

**The wake that reaches the cap is delivered.** The cap is the largest number of notifications an agent may receive, so the cap-th arrives; further pending changes for that agent are dropped, and its reads stay open. A late acknowledgment cannot restore an agent presumed failed, because the outcome would then depend on when the acknowledgment was read, and the audit has already recorded the failure.

**The batch window is a field on the `Premise` declaration**, which the board ignores and the control component reads. An agent's dispatch is due at the earliest due instant over its pending set, so a zero-window change dispatches at once and sweeps along everything pending. Rejected: a parallel control-layer declaration type, two declarations that could disagree.

**The seed bypasses admission and the write budget, is audited as its own event, and activates the run.** Seed notifications count against wake caps and the notification budget, because a wake costs an inference whatever caused it; seed writes do not count against the write budget, because the seed is an input, not a proposed write. Rejected: seeding through `set_premise`, which could leave a half-open run behind a rejectable seed; a reserved writer name for the seed, which would require refusing an agent of that name.

**Budgets are required and finite, and a tripped budget closes the run at once.** The tripping write is refused, the tripping dispatch withheld; a run that fits its budgets exactly can still close complete. Rejected: optional budgets, because the specification states defaults for the rule and the predicate and none for budgets, and a run without a wall-clock bound can wait forever.

**One lock serializes control state, and application code never runs under it.** The three completion counters change and are read only under the lock. The termination predicate runs with the lock released; its verdict is honored only if the counters are still zero and the board has not moved since, otherwise it is discarded and the next transition re-checks. The admission rule's view can therefore be stale by the writes admitted while it ran; a register write closes that window with its expected version, and a level write does not. Rejected: running rules under the lock, because a slow rule would block dispatch and a reentrant one would deadlock.

**Run outcomes are a union of four frozen dataclasses**, so the outcome that names failed agents carries them and no field is valid only in some states. Rejected: an enum with detail fields beside it.

**The model exposes the board as `BoardReader` only.** Reads go to the board directly and cannot be refused; `append` and `set` stay out of typed reach, because a caller holding the full board could write around admission. `create_model` is the only creation path, and the clock is dependency injection, not a seventh input. Rejected: exposing the board itself; a public `Control` constructor growing keyword arguments increment by increment.

**The audit records exactly the specification's list.** Seed writes, accepted and rejected writes, notifications, acknowledgments, extensions, presumed failures, wake-cap events, budget events, and the closing state. A conflicted register write is audited nowhere, because it never reached the board and the specification's list closes the set. Rejected: run-opened and write-conflicted events.

## Consequences

- Timed behavior is observable through `ManualClock.advance` with no waiting on the operating system clock.
- An application chooses its own concurrency: callbacks may run agents inline, hand off to threads, or schedule inference calls.
- The public surface grows across issues 21 to 24 by adding names, never by changing shipped signatures.

## Alternatives rejected

Each decision above names its rejected alternative in place.
