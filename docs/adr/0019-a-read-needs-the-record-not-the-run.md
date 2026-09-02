# ADR 0019: A read needs the record, and a run has two ends worth telling the caller about

Date: 2026-09-02

## Status

Accepted. Extends ADR 0014 on what an operation needs behind it.

## Context

ADR 0014 put every operation behind a `Control`, because that is where admission, ordering and the run's limits live. That is right for a write and wrong for a read.

`docs/limits.md` tells a reader to scale by putting different boards on different replicas and routing by board identifier. Under ADR 0014 that made reads unroutable: an agent had to reach the one replica holding the run before it could read a record every replica could serve, and a board whose run had finished and left the registry answered 404 while its contributions sat in the store.

The two ends of a run had a related gap. `create_model` registers the roster before returning, and registration runs each agent's callback on the calling thread, so an agent that reads back through the service met 404 for the board that was creating it. There was no point at which the caller could put the board in its registry first. At the other end nothing told the service a run had closed, so the registry both guides show grew for the life of the process, and the only ways to notice were polling every entry or blocking a thread per run.

## Decision

**A read needs the record. A write needs the run.**

`BoardService` takes an optional `store`. When no run is held for a board, the four `GET` operations are answered from a reader over that store, and the three writes keep answering 404. A board the store never held still answers 404, checked with `read_regions`, so a mistyped identifier is not answered 200 with an empty list. A live run is preferred where there is one.

`reader_for(store, board_id)` is exported, because a caller that wants a read handle without a run should not have to build one from a private class.

**A run tells the caller when it opens and when it closes.** `on_open` is called with the `Model` once the premises hold their values and before the first agent is registered. `on_closed` is called once with the outcome, on whichever thread closed the run, with the lock released.

Both take the contract `Agent.notify` already has: application code at the library's boundary, must not block, exception suppressed. A router that is down must not abort a run that has opened.

## Consequences

Any replica holding the store answers a read for any board in it, which is what routing by board identifier needs. Writes stay bound to the one replica holding the run, which is the constraint the library actually has.

A read answered from the store bypasses nothing that a read through a `Control` would have applied. Reads never passed through admission and never consumed anything, so the two paths return the same rows.

The audit is the exception, and stays so. It lives in the process, and no operation on the wire exposes it.

`outcome()`, `wait_closed()` and `on_closed` are now three ways to learn a run ended. They answer different questions: what happened, block until it does, and tell me when it does. A service holding many runs wants the third, and polling the first per run or blocking a thread on the second per run are what it was left with.

Answering a read from the store means `BoardService` can be given a store it holds no runs for. That is deliberate: it is the read-only replica case.
