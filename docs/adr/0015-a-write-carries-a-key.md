# ADR 0015: A write carries a key, and the key lives on the row

Date: 2026-09-02

## Status

Accepted.

## Context

A write that crosses a network can be sent twice. The client sends it, the connection drops before the answer arrives, and the client cannot tell whether the board took it.

Retrying appends the contribution twice. A level holding one finding twice is not the same board: an agent counting findings counts wrong, an admission rule reading them decides wrong, and nothing in the record says which of the two was the accident. The damage is silent and permanent.

So the agent client did not retry a write at all. A blip that costs a read nothing cost a write the whole call, and the agent was handed an exception for a request that had probably landed.

## Decision

A write carries an optional `idempotency_key`, and a store writes one key once.

A key the store has already written answers with what that write produced, marked `repeated`, and adds nothing. A key sent for a region it did not name before raises `IdempotencyKeyError`, because that is a mistake rather than a retry. Without a key nothing is deduplicated, which is what an in-process caller wants: it knows whether its own call returned.

### The key lives on the row, not beside it

The alternative is a table of keys the adapter writes alongside the contribution. Both are then correct only if both are in one transaction, and every adapter has to get that right separately.

On the row there is one insert. The row and its key cannot disagree, because there is no second write to lose. The uniqueness of the key is a unique index rather than something the adapter remembers, so two processes writing under one key are separated by the database, which is the only thing that sees both of them.

`Written.version` moves onto the row for the same reason. Answering a repeated premise write means answering with the version that write produced, and the row is where that is now recorded.

### A conflict uses no key

A premise write that names a stale version stores nothing. Having stored nothing it has no outcome to repeat, so its key stays unused. The caller reads the premise, decides again, and sends the same key.

### A key belongs to one board

Two runs share nothing else, and a store holds many boards. Keys are therefore scoped by board, and the unique index is on the pair.

### The same key with different content is not detected

A repeat returns the first write whatever content comes with it. Detecting a changed body needs the two contents compared, and comparing them means normalising what JSONB and BSON did to them on the way in, which is where a false mismatch would come from. A retry is expected to send what it sent before, and a caller that reuses a key for a different write in the same region gets the first write's answer.

Reuse across regions is caught, because the region is on the row already and costs nothing to compare.

## Consequences

`BoardStore.append` returns `Written` rather than an `int`, so it can say a write was a repeat. Anyone who wrote a store implements two more parameters and one changed return.

`SqliteStore` and `PostgresStore` add the two columns to a table written by an earlier version rather than assuming them. `MongoStore` indexes the key only where a key is present, so documents written before this change do not collide with one another.

The agent client can now retry a write against a blackboard at this version or later. It does not do so by default: an organisation that upgrades its agents before its blackboard would otherwise get exactly the silent duplication this change exists to prevent. That is a separate decision, recorded where it is made.
