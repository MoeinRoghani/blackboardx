# ADR 0030: The intent to notify is on the record

Date: 2026-09-05

## Status

Accepted. Completes what ADR 0025 began about who is notified.

## Context

A contribution committed to the store. The intent to notify lived in process memory, on a queue inside `HttpNotifier`. A process that committed a write and stopped between the two lost the notification silently, which is the dual write problem in its plainest form, and `docs/limits.md` documented the loss.

Two things made it worse than it sounds. The queue holds a notification through repeated backoffs capped at thirty seconds, so a slow agent's notification sits there for minutes rather than milliseconds. And the driver is not crashes: a rolling deployment replaces every replica on a schedule, and anything queued at that instant is gone. The window is widest exactly when it hurts most, because the agent was already struggling.

The watermarks from ADR 0025 could not close it. `mark_notified` advances at dispatch, before delivery is confirmed, so a send that raised left the watermark advanced and every other replica saw nothing owed. **The watermark records intent, not delivery.**

## Decision

`append` and `set` take the agents that should hear of the write and record one row for each, in the same transaction as the contribution. There is no second system to commit against, so there is no dual write to lose: the rows land with the write or neither does.

The row carries the sequence the write took, which is also the identifier the notification will carry, so nothing is allocated or matched up later.

**A row is marked sent only after the send returns.** Marking first would be at most once and would lose exactly what this exists to keep.

`Control.relay` sends what is unsent for the agents that process holds, because a process can reach no other. It is a plain method scheduled beside `close_expired`.

## Consequences

Delivery is at least once, so a notification may arrive twice. That costs nothing: a notification carries no values, the agent reads the board either way, and cumulative acknowledgment absorbs the extra identifier.

Marking is cumulative, for the same reason acknowledgment is. A notification covers a range, so sending it answers every row inside that range; marking one row exactly would leave the rest of a batched range unsent and the relay would resend work that had arrived.

A row says which agent and how far, not which regions. The relay takes the regions from the record, between that agent's answer and the write, so a resent notification names what a first one would have named.

`BoardStore` gains two operations and `append` and `set` gain an argument, so a store written against the previous release no longer satisfies the protocol.
