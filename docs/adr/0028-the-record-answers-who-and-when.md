# ADR 0028: The record answers who wrote and when

Date: 2026-09-05

## Status

Accepted.

## Context

A contribution carried a sequence and content. Who wrote it and when it was written existed only as an audit event in one process's memory, so both died with that process, and a record read back later could not say either.

Who wrote something and when it happened are properties of the write, the way the sequence is. They belong on the record.

## Decision

`Contribution`, `PremiseState` and `BoardChange` each carry `writer` and `written_at`.

**The instant is the store's, never the caller's.** Postgres and MongoDB stamp it server side with `now()` and `$$NOW`, SQLite uses `strftime` inside the statement, and the in-memory store uses its own clock. No instant crosses a call boundary in either direction.

That is not a detail. Agents run as separate services and their clocks disagree. The sequence already orders writes against each other; the instant answers a different question, being when in the day something happened, and a wrong clock makes that unanswerable in a way ordering cannot repair.

**Both read as nothing where there is nothing to say.** An opening premise value names no writer, because the application supplied it before any agent registered. A record written by an earlier version carries neither.

## Consequences

The record answers after the process that wrote it is gone, which is what an incident review needs and what the audit could not do. This is one of the two facts that made `Control.read_audit` removable.

The wire bodies carry both as optional, and `written_at` crosses as an ISO-8601 string, so an older blackboard sends neither and a newer agent decodes `None` for both.

The schema number rises, and `append` and `set` take a `writer`. A store written against the previous release no longer satisfies the protocol.

A repeated idempotency key still answers with the first write, so a second sender's name does not overwrite the first's.
