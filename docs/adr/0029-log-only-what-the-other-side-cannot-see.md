# ADR 0029: Log only what the other side cannot see

Date: 2026-09-05

## Status

Accepted.

## Context

The library had two log lines, both about a notification that failed to arrive. Nothing said what should be added as more of the run moved into the store, and the risk was the ordinary one: a library that logs its happy path fills an aggregator with lines nobody reads, next to the agent's own lines about the same events.

## Decision

One rule. **Log only what the other side cannot see.**

| Event | Logged | Why |
| --- | --- | --- |
| A notification undelivered after its retries | Yes | The agent cannot log what never arrived |
| A retry | Yes | Same |
| A run closing, with its outcome and unfinished agents | Yes | No agent learns the run ended, or that it was named unfinished |
| A store or schema error | Yes | Nobody else sees it |
| A write accepted | No | The record is the log: it carries the sequence, the writer and the instant |
| A write rejected | No | It is returned to its caller as a value |
| A notification dispatched | No | The agent logs receiving it |
| An acknowledgment | No | The agent logs sending it |

Standard library logging, lazy `%s`, one module logger. The application configures handlers and format.

## Consequences

The duplication concern is answered structurally rather than by discipline. A line from this library and a line from an agent are never about the same fact, so there is nothing to deduplicate when both land in the same aggregator.

Everything on the happy path stays silent, so a busy run writes nothing until something goes wrong.

Two failures that reached nobody now reach a log. A schema this build cannot read raises to its caller, but a store opened at start-up or read by a scheduled sweep raises where nobody is reading. And a sweep has no caller at all, so a store failing under `close_expired` was silent; it is logged, and the sweep continues to the next board rather than ending the pass.
