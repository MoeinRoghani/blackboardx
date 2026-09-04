# ADR 0024: A run is kept in a store, the way the record already is

Date: 2026-09-04

## Status

Accepted. Supersedes the part of ADR 0014 that makes a write reach the one process holding the run, and extends ADR 0013 on a store holding many boards.

## Context

The library divides what a blackboard holds into two parts, and keeps them in two places.

`BoardStore` holds the record: the declared regions, the contributions, each premise's value and version, the sequence, and the idempotency keys. Every call names the board, so one database serves every board an application runs, and `PostgresStore` states that its two guarantees hold across processes rather than across the threads of one. Two processes writing to one board produce one gapless sequence and one winner for a premise write.

`Control` holds the run in the memory of the process that built it: the agent registry, the notifications outstanding against each agent, each agent's cursor, the counter that numbers notifications, the audit, and the wall clock and idle deadlines. Nothing writes any of that down.

One board is therefore served by one process, and `docs/limits.md` tells an application to scale by putting different boards on different replicas and routing by board identifier.

Two costs follow, and only the first is about replicas.

**A deployment that cannot route by board identifier cannot run more than one replica.** An application behind a single name, load balanced across two replicas with no affinity, has every agent's write answered by whichever replica the balancer picked. That is the right answer for the replica to give, and it is useless to the agent, which has one address and no way to reach a different replica. The application is then a single process wearing a replica count.

**A notification decided in memory is lost when the process stops.** The control component works out which agents a change concerns and calls each one's `notify`, holding no lock. The process can stop after the write has committed and before that call is made. Nothing records that the notification was owed, so the run waits for an acknowledgment no agent knows to send, until the idle deadline closes it. `blackboard.delivery` documents the same hole one layer out, for the queue behind `HttpNotifier`.

The second cost is present with one replica, so routing does not answer it.

## Decision

**A run is kept in a store, the way the record already is.**

`RunStore` is a protocol beside `BoardStore`. `Control` takes one, and holds through it:

- the agent registry: each agent's name, the regions it subscribes to, the levels it may write, and its cursor,
- the notifications issued against each agent, the range each covers, and which are still unacknowledged,
- the counter that numbers notifications on this board,
- the wall clock and idle deadlines,
- the outcome, once the run closes.

Three things stay in the process, because each is derived rather than held.

`Agent.notify` is a callable and cannot be a row. The address behind it belongs to the application, which rebuilds the callable on each process from whatever it recorded. A region's kind and its batch window come from the declarations the caller passes to `create_model` or `attach_model`, which every process has. The audit is what one process observed, and ADR 0019 already says it lives in the process and no operation on the wire exposes it.

**The default holds the run in memory.** `InMemoryRunStore` implements the protocol over dictionaries, and `Control` uses it when the caller names none. An application embedding a blackboard in one process therefore sees no change and pays no round trip. Naming a store is how an application asks for a run more than one process can serve, exactly as naming `PostgresStore` is how it asks for a record more than one process can read.

**A deadline is a value, and a sweep is what notices it passing.** `Control` still arms a local timer, which is what closes a run promptly in one process. With a store the deadline is also written down, and `sweep(store, clock)` closes every run whose deadline has passed, from any process. Closing is a conditional write on the outcome still being unset, so a timer and a sweep racing, or two sweeps racing, produce one winner and one outcome.

**A dispatch is reconciled against the record, not assumed.** The run store records the highest sequence it has issued notifications for. A process that stopped between the write committing and the notification being issued leaves that number behind the board's own head, and the next write or sweep issues notifications for the gap. A notification carries no values and names a range, so one covering a wider range than it strictly had to costs an agent one read.

## Consequences

Any replica holding both stores serves any operation on any board. The routing requirement in `docs/limits.md` becomes an optimisation for locality rather than a condition for correctness, and an application behind one name with no affinity works.

`attach_model` stops being a recovery path. It opens a run over a board that already holds a record, which is what every process does for a board it did not create, so it becomes the ordinary way a second replica picks up work rather than the thing a restart does.

A write costs a round trip to the run store on top of the one to the board. For a board taking a handful of contributions from a handful of agents this is not measurable against the network hop the agent already made. For a board taking hundreds of writes a second it is, and such an application should hold its run in memory and route by board identifier, which the default still does.

**A batch window shorter than the sweep interval is not honoured by the sweep.** A window collapses a burst of writes into one notification, and with a store the due time is a value like any deadline. The local timer still fires it promptly in the process that took the write. A process that stopped before it fired leaves the notification to the sweep, which finds it no sooner than its own interval. An application wanting a window measured in milliseconds to survive a process ending has to sweep that often.

The run store grows. Every notification issued against every agent of every board is a row, and nothing in this library deletes one, for the reason ADR 0013 gives for `BoardStore.delete`: retention is a decision the application makes and the control component makes none. `RunStore.delete` removes one board's run state, and nothing in the library calls it.

The audit stays process-local, and is now the only part of a run that is. A process that did not take a write did not observe it, so its audit is shorter than the audit of the process that did. That was already true of two processes serving one board and is now reachable without a restart.
