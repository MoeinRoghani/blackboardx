# A run that lives in the database

**Status: built.** What this document proposed is in the library. It is kept
because the reasoning is still the reasoning, and because the parts it got
wrong are worth having on the record.

The decisions are in
[ADR 0024](../adr/0024-the-run-is-in-the-store.md),
[ADR 0025](../adr/0025-the-identifier-is-the-range.md) and
[ADR 0026](../adr/0026-one-door-into-a-board.md). What the library holds and
where is in [What this version does not do](../limits.md).

## What was missing, and is not now

The board was durable and the run was not. `Control` held the registered
agents, what each was owed, the audit and the two deadlines in process
memory, so one board was served by one process at a time and losing that
process ended the run against a record that survived it.

A run's deadlines, its outcome, and how far each agent has been notified and
has answered are now rows. Any process reads them, closes a run that has gone
quiet, and names the agents it did not hear back from.

## Where this document was wrong

**It proposed a second store.** Live coordination was to go in a `RunStore`
backed by Redis while the record stayed in a `BoardStore`. Two stores cannot
commit together, so a write could land and its deadline fail to move. The
library has one store and one transaction instead, which is the reason the
dual write does not arise.

**It proposed an audit table.** Every event of a run, written hot and read
cold. There is none. What it recorded is answered two other ways: a
contribution carries its writer and the instant the store stamped, and
everything else is a log line. `Control.read_audit` is deprecated and may be
removed on or after 2026-12-05.

**It proposed storing each agent's callback address, subscriptions and
permissions.** Those are configuration, which the application hands to every
replica the way it hands the regions, the limits and the admission rule. A
store holds run state; it does not hold a callback.

## What is still to build

The transactional outbox. A contribution and the intent to notify are not yet
written in one transaction, so a process that commits a write and stops
before delivering loses the notification.

## What did not change

The model. Regions, admission, subscription, notification, and the three
outcomes behave the same whether a run is held in a process or in a database,
because none of them depends on where the run is held.

A notification still carries no values, so a delivery attempted twice still
costs nothing.

A write still takes its sequence by incrementing a counter inside the writing
transaction rather than from a database sequence. A database sequence does not
roll back, and a gap is a hole in a record whose numbers are addresses.
