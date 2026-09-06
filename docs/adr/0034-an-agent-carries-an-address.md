# ADR 0034: An agent carries an address

Date: 2026-09-06

## Status

Accepted. Says how ADR 0032 is built.

## Context

ADR 0032 decided an agent is part of the run and is written where the run is written: the name, what it subscribes to, what it may write to, and where to reach it. Three of those four are already fields on `Agent`. The fourth is not there at all.

`Agent.notify` is a callable, and in a deployment it is `notifier.to(url)`, which closes over the address. The library is handed the closure and never the address, so it cannot write down what it never sees. That is the whole of what blocks ADR 0032.

## Decision

`Agent` gains `address`, a string naming where the agent is reached. `notify` becomes optional, and a declaration carries at least one of the two.

| Given | Means |
| --- | --- |
| `notify` only | An agent in this process. Nothing to write down but the name and the subscriptions, and no other process can reach it. |
| `address` only | An agent reached over the wire. Any process holding a transport reaches it. |
| Both | The address is written down and the callable is used where it is present, which is the fast path in the process that has it. |

The store holds the name, the subscriptions, the write permissions and the address. It does not hold the callable, and no adapter has a column for one.

**A transport turns an address into a delivery.** `Control` takes one, and it is the only thing a process needs that the store cannot supply. `HttpNotifier` provides one; an application sending over something else writes its own. A process with no transport reaches the agents it holds callables for and no others, which is what it can do today.

## Consequences

Which process took which request stops deciding anything. A write reaching any replica records the right rows, because who should hear it is read from the store rather than from that replica's own roster. A relay on any replica sends them, because the address is on the record and the transport is in the image.

An agent that joins mid-run through `register_agent` is written down the same way and by the same code as one named at creation. The two doors stop differing in what they leave behind.

An agent declared with `notify` alone keeps working and keeps its limit: it exists in one process, and that process is the one that wakes it. That is what an in-process run is, and it stays supported without a store column for it.

`_who_hears` reads the store rather than the local roster, so a process that holds no agents still records that a write should wake one.

The schema number rises. `read_agents` answers with the declaration beside how far the agent got, so a caller reads one row rather than two.

## Rejected

**Storing the callable.** A function is not data. Every attempt to make it data ends at a name the store resolves against code the process already has, which is an address by another name and a worse one.

**Leaving the address out and storing only the subscriptions.** A replica would then record the right rows and still be unable to send them, so the notification would wait for whichever process happened to hold a callable. That is the defect with a smaller blast radius, not a fix.
