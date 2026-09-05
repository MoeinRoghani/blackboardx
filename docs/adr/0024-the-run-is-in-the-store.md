# ADR 0024: A run's deadlines and outcome are in the store

Date: 2026-09-05

## Status

Accepted. Completes ADR 0009 on where silence is measured, and supersedes what ADR 0018 decided about opening a run over a record.

## Context

The record was durable and the run was not. The two deadlines were timers held by one process, so a run closed only where that process was, and losing that process ended the run.

That is survivable for one process and wrong for a deployment. A run closes because nothing happened, and nothing happening means no request is in flight, so the process that opened the run is the only one that could ever notice. A second replica holding its own control component for the same board measures silence from its own start and knows nothing of the first.

Two stores were considered and rejected. Keeping the run in Redis beside the record in Postgres means writing to two systems in one operation, which is the dual-write problem: a contribution that commits while the deadline push fails leaves a run that can close on a board just written to. The answer the field gives to that is to not have the dual write, so the run extends the store the record already lives in and both move in one transaction.

## Decision

`BoardStore` gains five operations, and no second protocol and no second backend exist.

`open_run` records that a run is open and sets both deadlines. `touch_run` pushes the idle deadline out. `close_run` records how the run ended. `read_run` answers with the deadlines, the outcome, and the store's own clock beside them. `runs_past_deadline` answers with the boards whose run is open and past a deadline.

**The store's clock decides.** Every deadline is computed by the store, and `read_run` returns the store's `now` with them, so a caller decides a deadline has passed by comparing two instants that came from one clock. A pod's clock never enters it. Pods disagree about the time and a store does not, and a skewed pod would otherwise close runs early across a fleet.

**Closing is a write only one caller wins.** `close_run` answers `True` to the caller that recorded the outcome and `False` to every other. A control component that loses adopts the outcome the winner wrote rather than its own. However many callers reach a deadline together, the run closes once, one outcome is recorded, and one `on_closed` fires. No lock, no lease, no leader and no owner appears anywhere, because the store is the arbiter.

**Closing has two triggers, and they are the same write.** A process holding a run keeps its timer, which closes the run promptly where it is. `close_expired` closes the runs no process is watching. Both call `close_run`, so both are safe together.

**`close_expired` is a function, not a deployment.** The library takes no view on where it runs: a thread beside the service, a scheduled job, a test advancing time by hand. A library that tells an application to deploy a scheduler has stopped being a library.

## Consequences

Any process holding the store can close any run, which is what a deployment of more than one replica needs and what a single process is unaffected by.

The Postgres table is `blackboard_run_state` and the MongoDB collection matches, named apart from the `blackboard_runs` the withdrawn 0.11.0 created. That release added a table without raising the schema number, so a database that saw it holds one with different columns and a `NOT NULL` where this design writes nothing. `CREATE TABLE IF NOT EXISTS` would leave it alone and every run operation would then fail with a missing column, which is the failure the schema stamp exists to turn into a refusal at the door.

`BoardStore` is thirteen methods rather than eight, and the conformance suite grew with them, so anyone holding a store of their own is told what is newly required by a failing test rather than at run time.

The registry, the cursors, the outstanding notifications and the notification counter are still held in the process at this point. This change moves what decides when a run ends; ADR 0025 moves what decides who is notified.
