# ADR 0025: A notification is identified by the range it covers

Date: 2026-09-05

## Status

Accepted. Completes what ADR 0024 began, and supersedes what ADR 0018 recorded about a cursor and a notification identifier starting again.

## Context

ADR 0024 moved the run's deadlines and its outcome into the store, so any process closes a run that has gone quiet. What decides who is notified stayed behind: the registry, every agent's cursor, the outstanding notifications and a counter allocating identifiers, all in the memory of one `Control`.

That left the deployment half solved. A write reaching a replica where the agent never registered landed on the record and woke nobody, because the process holding the write held no callback and the process holding the callback did not know the write had happened.

Two things had to be answered to move the rest. What does a store hold per agent, and where does a notification identifier come from when two processes both issue notifications and neither can see the other's counter.

## Decision

### Two watermarks, not a set of notifications

The store holds two numbers per agent: how far it has been notified, and how far it has answered. Both are sequence numbers on the board.

Nothing was lost by collapsing the set. `_outstanding` was keyed by notification, but no code read an individual member: closing projected agent names, re-registration deleted by agent, and the count was read nowhere. An agent owes an answer exactly when it has been told further than it has answered, which is a comparison of two integers.

Both only rise. Two processes notifying one agent leave the higher of what they wrote, whichever order they arrive in, so nothing orders them and no lock is taken. `acknowledge` is the one operation where a compare-and-set earns its keep: it answers with the entry as it stood before the call, so of several callers naming one sequence exactly one is told it was the first.

### The identifier is the end of the range

A counter in one process cannot be allocated from another. Rather than distribute it, the counter is deleted: a notification is identified by the sequence its range ends at.

The cumulative acknowledgment rule already compared nothing else. An agent answering a range answers every range that ends at or before it, so the identifier and the thing being compared were always the same number, spelled twice.

Two processes issuing one identifier have issued one instruction: the same agent, the same range, and therefore the same acknowledgment. That is not a collision, it is a fact about the ranges.

### The process holding the agent is the one that notifies

The writing process records that the write landed and nothing about who should hear of it. The process an agent registered with is the only one that can reach it, so it is the one that reads the board and decides. `notify_due` is that read, and it is a plain method the application schedules the way it schedules `close_expired`.

A batch window needs nothing in the store. The store already stamps a contribution with the instant it landed and answers with its own clock beside it, so what is left of a window is one instant subtracted from the other. Both came from the store, which is what makes the subtraction mean anything across processes that disagree about the time.

## Consequences

A write taken by any replica reaches an agent registered with any other. That is the property the library was missing, and it is now held by a test rather than described.

A cursor survives the process. An agent registering against a replica that never saw it resumes from what it answered instead of being told the whole board again, so work it had finished is not done twice.

`close_expired` names the agents a run did not hear back from. It could not before: a sweeper holds no declarations, so it passed an empty set and a run closed by the sweep said nobody was unfinished when some were.

A notification covering an empty range is no longer issued. A returning agent that had answered everything was previously woken with a range starting after it ended, which the old test asserted and its own comment described as covering nothing.

Two agents told of one write now carry one identifier. It names the work rather than the delivery, and each agent answers for itself.

`BoardStore` is sixteen methods rather than thirteen. A store written against 0.11 no longer satisfies it, which is a breaking change carried by the release and the migration page.

The agent roster is not run state and is not moved. It is configuration, and it sits in `create_model` beside `regions`, `limits`, the admission rule and the termination predicate, each of which the application hands to every process and none of which a store holds. Run state is not split by this change or any other: it is wholly in whatever store was chosen, held in memory by `InMemoryStore` and in the database by `PostgresStore`, through one code path either way.

What follows from that is a deployment rule rather than a storage one. Replicas are given the same roster because they are given the same configuration, so each can reach each agent. An application that instead calls `register_agent` on one replica at run time has told one replica something the others were not told, which is the same mistake as running replicas with different configuration.
