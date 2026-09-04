# A run that lives in the database

**Status: a design, not a thing that exists.** Nothing here is in the library.
What the library does hold, and where, is in
[What this version does not do](../limits.md).

This replaces an earlier document that described a blackboard service, an
agent client, and an HTTP surface. All three are now built, and the surface
they were built with is in `blackboard.wire` and
[ADR 0014](../adr/0014-the-agent-facing-protocol.md), not here. What that
document described and the library still lacks is the part below.

## What is missing

The board is durable and the run is not. `Control` holds the registered
agents, the outstanding notifications, the audit, and the two deadlines in
process memory, so one board is served by one process at a time and losing
that process ends the run against a record that survives it.

A durable run means a replacement replica picking up where the last one
stopped, and it means any replica serving any board, so a pod keeps nothing
between requests.

## What would move into the store

| Table | Holds | Exists |
| --- | --- | --- |
| `regions` | The named regions of a board and their kind | yes |
| `premises` | The current value and version of each premise | yes |
| `contributions` | Every write, with its sequence, region, version, and idempotency key | yes |
| `boards` | One row per board: its sequence counter | partly; no status, outcome, or limits |
| `agents` | Each registered agent: callback address, subscriptions, write permissions, cursor | no |
| `notifications` | Every notification issued, delivered or not, acknowledged or not | no |
| `audit` | Every event, in the order each occurred | no |

The three that do not exist are exactly the three `Control` holds in memory.
The `boards` row would gain the run's status, its outcome, and its two limits,
so that a replica reading it learns what the run is and when it ends.

## What would replace the timers

`Control` arms a timer for the wall clock and another for the idle limit,
which is why they die with the process. A durable run cannot hold a timer, so
the deadlines become columns and a sweep reads them.

Every replica would run the same loop, taking rows with
`FOR UPDATE SKIP LOCKED` so that no two act on the same row and none waits on
another:

| Query | Action |
| --- | --- |
| Boards past their wall clock limit | Close as `WallClockExpired` |
| Boards silent for their idle limit | Close as `Settled`, naming any agent that did not finish |
| Notifications undelivered past a delivery timeout | Record the agent unreachable and stop retrying |
| Notifications undelivered within it | Attempt delivery again |

The last two would move `HttpNotifier`'s queue into the database with them,
which is what makes a queued notification survive a restart.

## What would not change

The model. Regions, admission, subscription, notification, and the three
outcomes behave the same whether a run is held in a process or in a database,
because none of them depends on where the run is held.

A notification would still carry no values, so a delivery attempted twice
still costs nothing.

`contributions.sequence` would still be taken by incrementing a counter row
inside the writing transaction rather than from a database sequence. A
database sequence does not roll back, and a gap is a hole in a record whose
numbers are addresses.

## Why it is not built

It is a large change to the control component and to every store, and the
deployment it enables, several replicas behind one board, is not one the
maintainer has needed. The deployment that works today, one board to one
replica and routing by board identifier, is documented in
[Running as a service](../concepts/service.md) rather than left to be
discovered.
