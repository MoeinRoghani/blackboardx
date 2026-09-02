# ADR 0016: What the application settles raises, what the run decides returns

Date: 2026-09-02

## Status

Accepted. Supersedes ADR 0002 on the membership of `RejectionCause` and on where an undeclared region is reported.

## Context

One wrong region name produced three different shapes, decided by which method the caller happened to reach.

| Call | Region | Answer |
| --- | --- | --- |
| `control.write` | never declared | `Rejected(UNDECLARED_REGION)` |
| `control.write` | declared as a premise | raises `RegionKindError` |
| `reader.read_level` | never declared | raises `UndeclaredRegionError` |
| `control.register_agent`, `writes_to` | declared as a premise | raises `UndeclaredRegionError` |

A caller handling one mistake had to write three branches. The last row is worse than inconsistent: it says a region is undeclared when the region is declared, so the message is false.

ADR 0002 set the rule as "refusals return as values; defects raise", and put `UNDECLARED_REGION` among the returned refusals. That was not wrong so much as unfinished: it never said which side of the line a given condition falls on, so each new condition was decided on its own.

## Decision

The axis is what decides the condition, not how severe it is.

**What the application's own configuration settles raises.** The application declared the regions. A name outside them is a defect the application can fix before the run starts, it is the same defect on every path, and no retry changes it.

**What the run's policy decides returns.** The admission rule, the writer's permissions, the run being closed, and a premise version that moved on are decisions this run made about a write it understood. The writer must handle them, and correct agent code races them.

So an undeclared region raises `UndeclaredRegionError` everywhere, matching the kind mismatch beside it and the reader below it. `RejectionCause.UNDECLARED_REGION` is removed, because it never described a decision the run made.

Registering an agent to write to a region declared as a premise raises `RegionKindError` saying so, rather than `UndeclaredRegionError` naming a declared region.

`_refuse_region` raises a named `TypeError` for a region that is not a string, rather than letting a dictionary lookup raise "unhashable type".

## Consequences

`RejectionCause` has four members. Every one is a decision the run made.

The audit loses `WriteRejected(cause=UNDECLARED_REGION)`. That is consistent: a kind mismatch already raised and was audited nowhere, and the audit records what a run decided rather than what a caller got wrong.

Over HTTP the same request moves from 422 with `cause: undeclared_region` to 404 with `error: unknown_region`. Both halves already carried that translation for reads, so neither needed changing. The move is free because `blackboard.server` is absent from every released tree, so no deployed agent branches on the old status.

A caller that caught `UndeclaredRegionError` around `register_agent` now also meets `RegionKindError`. Both are `BlackboardError`.

No deprecation window applies. A call cannot both return and raise, and no name leaves `__all__`.
