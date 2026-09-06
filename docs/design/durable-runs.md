# A run that lives in the database

**Status: built.** What this document proposed is in the library. It is kept
because the reasoning is still the reasoning, and because the parts it got
wrong are worth having on the record.

The decisions are in
[ADR 0024](../adr/0024-the-run-is-in-the-store.md),
[ADR 0025](../adr/0025-the-identifier-is-the-range.md) and
[ADR 0026](../adr/0026-one-door-into-a-board.md), and what it settled about
the agent was corrected by
[ADR 0032](../adr/0032-an-agent-is-part-of-the-run.md) and
[ADR 0034](../adr/0034-an-agent-carries-an-address.md). What the library holds
and where is in [Storage](../concepts/storage.md#what-a-store-holds).

## What was missing, and is not now

The board was durable and the run was not. `Control` held the registered
agents, what each was owed, the audit and the two deadlines in process
memory, so one board was served by one process at a time and losing that
process ended the run against a record that survived it.

A run's deadlines, its outcome, and how far each agent has been notified and
has answered are now rows. Any process reads them, closes a run that has gone
quiet, and names the agents it did not hear back from. The write that closes a
run removes what only an open run needs, leaving the outcome.
[ADR 0033](../adr/0033-closing-clears-what-the-run-needed.md).

## Where this document was wrong

**It proposed a second store.** Live coordination was to go in a `RunStore`
backed by Redis while the record stayed in a `BoardStore`. Two stores cannot
commit together, so a write could land and its deadline fail to move. The
library has one store and one transaction instead, which is the reason the
dual write does not arise.

**It proposed an audit table.** Every event of a run, written hot and read
cold. There is none. What it recorded is answered two other ways: a
contribution carries its writer and the instant the store stamped, and
everything else is a log line. `read_audit` went in 0.13.0.

## Where the correction above was itself wrong

**It proposed storing each agent's callback address, subscriptions and
permissions.** This page called those configuration and said a store does not
hold a callback. Four of the five fields on a declaration are data, and the
one that is not is the callable rather than the address. An agent is now
written to the run through both doors, and a replica that holds no callable
for it reaches it at the address the run records.
[ADR 0032](../adr/0032-an-agent-is-part-of-the-run.md) and
[ADR 0034](../adr/0034-an-agent-carries-an-address.md).

## What it left to build, and is built

The transactional outbox. The intent to notify is written in the transaction
that writes the contribution, and a relay sends what is unsent and marks it
only once the send returns, so a process that commits a write and stops before
delivering loses nothing.
[ADR 0030](../adr/0030-the-intent-to-notify-is-on-the-record.md).

## What did not change

The model. Regions, admission, subscription, notification, and the three
outcomes behave the same whether a run is held in a process or in a database,
because none of them depends on where the run is held.

A notification still carries no values, so a delivery attempted twice still
costs nothing.

A write still takes its sequence by incrementing a counter inside the writing
transaction rather than from a database sequence. A database sequence does not
roll back, and a gap is a hole in a record whose numbers are addresses.
