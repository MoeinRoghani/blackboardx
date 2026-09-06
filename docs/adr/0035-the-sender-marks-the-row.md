# ADR 0035: What sends the notification marks the row

Date: 2026-09-06

## Status

Accepted. Says how ADR 0030 holds for a callable that queues.

## Context

ADR 0030 put the intent to notify on the record so that a process which commits a write and stops before delivering has not lost the notification. The control component marks that row sent when `Agent.notify` returns, after the send and never before, which is what makes delivery at least once.

`notify` is an opaque callable, and the one the library ships for a deployment is a lane. A lane puts the notification on a queue and returns, which is the whole reason it exists: the agent that wrote is not made to wait, and one slow agent delays nobody else. So the call returns before anything reaches the wire, and the mark that was meant to follow the send follows the queue instead.

The row is then gone while the notification is still in memory. A rolling update replaces the process, and nothing recovers it. That is the scenario the outbox was built for, named in the design as the one that matters, and the outbox did not cover it.

## Decision

**What performs the send marks the row.** A lane sends, so a lane marks.

`HttpNotifier` takes the store. A lane whose notifier has one calls `mark_sent` after the transport returns, and answers `marks_sent` so the control component leaves the row alone. A notifier with no store answers `False`, and the control component marks as it did before.

`reach` is unaffected. It sends on the calling thread and returns after the send, so the caller that marks the row can see that it landed.

| The callable | Returns when | Marked by |
| --- | --- | --- |
| An application's own | It decides | The control component, on return |
| A lane, notifier with a store | The notification is queued | The lane, after the send |
| A lane, notifier without | The notification is queued | The control component, on return |
| `reach` | The send returned | The caller, after the send |

## Consequences

A notification a lane accepted and never sent is still owed, so `Control.relay` delivers it, from the process that comes back or from a replica that can reach the agent by address.

The marking is at least once in the case that matters and stays at most once in the case that was already at most once. A notifier with no store behaves exactly as it did, so nothing that exists breaks, and the store is what an application adds to get the guarantee.

`marks_sent` is read off the callable rather than declared in a protocol the application implements. Anything answering `True` takes on the marking; anything that does not is marked for. An application whose own callable defers its send can do the same.

The store reaches `delivery` as a type annotation and a method call, and nothing in `blackboard.delivery` imports it at run time.

## Rejected

**Never marking on the callable path.** The relay would then resend on every pass until the agent acknowledged. Duplicates are free on the wire and are not free for an agent that is a language model, where each one is an inference.

**Making `notify` report what it did.** A return value or a second protocol method changes the contract every application's callback is written against, to describe a case only the library's own transport has.

**Having the lane report through `on_failure` only, and restoring the row from there.** That puts the guarantee in the application's handler, which the library cannot require it to write.
