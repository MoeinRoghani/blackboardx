# ADR 0010: The acknowledgment deadline and the wake cap are removed

Date: 2026-08-21

## Status

Accepted. Supersedes the specification's acknowledgment deadline, its wake cap, and the `extend` operation.

## Context

The specification gives each agent an acknowledgment deadline and a wake cap, and gives an agent `extend` to buy more time before its deadline passes.

The deadline exists for liveness: without it one agent that never acknowledges holds a run open forever. ADR 0009 gave that job to the idle limit, which closes a run after sustained silence whether or not anything is outstanding. At closing time an unacknowledged notification already names the agent that never came back, so the deadline distinguishes nothing the record does not carry.

The wake cap exists to stop a runaway exchange between agents. The wall clock stops it, in the unit that the cost is actually measured in. Two bounds on the same thing, in different units, means an operator has to reason about both.

`extend` exists only to move a deadline.

## Decision

An agent declares its name, its delivery callback, and the regions it subscribes to and may write. Nothing else.

`extend` is removed, along with the timer that watched each notification, the presumed-failed state, and the capped state.

A notification no longer carries a deadline, because there is none.

The agents a run names as unfinished are those still holding an unacknowledged notification when it closes.

## Consequences

- One timer per notification is gone. The only timers a run arms are its wall clock and its idle limit.
- An agent that is slow and an agent that is dead are no longer distinguished while a run is open. Both are holding a notification. At closing time both are named unfinished, which is the fact a reader of the result needs.
- An agent takes as long as it takes. Nothing in the library asks it to promise a duration, which is what the deadline did, and nothing asks it to renegotiate one.
- A runaway exchange between two agents is bounded by the wall clock alone. That was already true, because the cap bounded wakes per agent while the exchange consumed time.

## Alternatives rejected

- **Keeping the deadline for reporting.** A deadline that ends nothing is a service level stated in the wrong place; the audit already carries when each notification was dispatched and whether it was acknowledged.
- **Keeping the wake cap as a per-agent guard.** It protects one agent from a loop while the run burns on regardless, and the wall clock ends both.
