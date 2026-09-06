# ADR 0036: The relay takes what the store cannot hold

Date: 2026-09-06

## Status

Accepted. Completes the loop ADR 0030 needs.

## Context

Two jobs need a schedule. Closing a run nobody is watching, and sending what a write recorded and nothing has sent. `close_expired` is a function over a store, so `Sweep` calls it and an application deploys nothing.

The relay was neither. `Control.relay` is a method, so the only way to run it on an interval was to write the thread. A library that tells you to deploy a cron job has failed at being a library, and half of that failure was shipped.

The reason the relay is a method is the same one ADR 0034 turns on. Sending needs the callables: the callback for an agent in this process, and the transport for one reached at an address. A store holds neither, because a function is not data. So a relay cannot be written as a function over a store alone, and pretending otherwise would mean putting a callable in the store.

## Decision

`relay_unsent(store, control_for, limit)` takes the store and a callable answering with the `Control` for a board. `Sweep` takes the same callable as `control_for` and runs both jobs in a pass.

`control_for` is what `BoardService` already takes, so an application that serves the board over HTTP passes the thing it already has.

A board the callable answers nothing for is left rather than treated as an error. Another replica holds what this one does not, and the row waits for it.

**Reaping runs before relaying.** Closing a run clears what it was owed, so a notification to a run about to end is not sent and then answered into a run that has closed, which would spend an agent's work on a result nobody reads.

A `Sweep` given no `control_for` reaps and nothing more, which is what it did before.

## Consequences

Two replicas relaying at the same instant send the same notification twice. That is the guarantee the outbox already carries: a row is marked only after a send, delivery is at least once, and a repeat costs nothing because a notification carries no values.

`Control.notify_due` stays out of the pass. It answers for the agents one process holds callables for rather than for a board, so running it against whichever `Control` a board resolves to would not be running it for the processes that need it. An application whose agents are reached by callback schedules that one beside the sweep, and one whose agents carry addresses does not need it.

## Rejected

**A relay that takes only the store.** It would have to resolve a callable from the record, which is the same rejected alternative as storing the callable: a name the store resolves against code the process already has.

**Relaying before reaping.** An agent woken by a run that closes a moment later has done work nobody will read. For an agent that is a language model that is an inference spent on a result that is discarded.

**A `Sweep` that requires `control_for`.** Every existing caller would break for a job many of them do not need, and the argument answers nothing for an application whose agents are all reached on the write path.
