# ADR 0009: A run closes on silence, and time is its only bound

Date: 2026-08-21

## Status

Accepted. Supersedes the specification's rule that a run closes when no work is outstanding, and two of its three run budgets.

## Context

The specification closes a run at the moment its three counters read zero together. That is sound when every agent is registered before the run opens, because the first quiet moment then means every agent has finished.

Agents now register at different times and are idle between wakes, so the counters read zero constantly: before the first agent registers, and in the gap between every pair of wakes. Closing at such an instant closes the run before the work.

The specification also bounds a run three ways: wall clock, total writes, and total notifications. A write is the cause of a notification and a notification is its effect. Limiting the effect means that past some count a change lands and no agent is told, while the run remains open and still accepts writes. A record whose changes reach nobody has stopped being shared, and it stops silently.

## Decision

A run closes when nothing has happened for its idle limit. Silence is measured from the last event, so every write, register write, registration and acknowledgment pushes the deadline out. Where the application supplies a termination predicate, the predicate is asked when that deadline passes, and answering continue re-arms it.

`RunBudgets` carries two durations, `wall_clock` and `idle`. Counting writes and counting notifications are both gone.

A run closes in one of three states, and each names the agents that did not finish, meaning those holding an unacknowledged notification, those recorded presumed failed, and those that reached a wake cap.

| Outcome | Cause |
| --- | --- |
| `Settled` | Nothing happened for the idle limit |
| `WallClockExpired` | The wall clock limit passed |
| `Aborted` | A caller closed the run |

## Consequences

- Why a run ended and which agents failed to finish are separate facts, so a run settles normally while one agent never returns. `Complete` and `FinishedWithFailures` conflated them.
- A change that lands is always notified to every agent that should hear it. No count can suppress a notification.
- A blackboard nobody joins stays open until its wall clock ends it, which is correct: the work never began.
- An idle limit is now required, so every caller states how long silence must last before its run is over.

## Alternatives rejected

- **Closing at the first quiet instant.** It closes a run before its agents have registered, and again in the gap between any two wakes.
- **Keeping the write budget.** The wall clock already bounds a run, and a second bound in a different unit buys nothing.
- **Keeping the notification budget.** It is the only limit that can break the record's one guarantee, and it does so silently.
