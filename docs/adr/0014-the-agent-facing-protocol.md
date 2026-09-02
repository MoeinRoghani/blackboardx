# ADR 0014: The library states the agent-facing protocol and answers it

Date: 2026-09-02

## Status

Accepted.

## Context

An agent that runs as its own service reads and writes over HTTP. Until now the library supplied the bodies that cross between the two halves and nothing else, so the platform team chose the rest: which path carries a level read, which status code carries a refused write, and how a stale premise version is told apart from an admission refusal. Every agent team then wrote a client against those choices.

Two teams inventing one protocol between them is what this SDK exists to remove, and it is where the halves drift. A blackboard that starts answering 409 for a refusal it used to answer 422 for breaks every agent that branched on the code, and nothing fails until an agent stops writing.

The operations were never in question. They are the ones the control component already has: read the declared regions, read a level, read a premise, read the board's changes, append to a level, set a premise, acknowledge a notification. What was in question was their shape on the wire.

## Decision

The library states the protocol and answers it. `blackboard.wire` holds the operation table, and `blackboard.server.BoardService` turns a request into a call on `Control`.

### One path per operation, not one endpoint for all of them

A2A puts every operation behind one JSON-RPC endpoint, which leaves a service with a single route to mount. This library gives each operation a path and a method.

The reason is the deployment this library is built for. Authentication and rate limiting sit in a gateway in front of the service, and a gateway reads the method and the path. Behind one endpoint every operation looks the same to it, so a read cannot be allowed where a write is refused, and a read-heavy agent cannot be given a different rate limit from a writer. With a path each, an existing gateway policy does that work and the service writes none of it.

The cost is that a service mounts more than one route. `BoardService` matches the path itself, so the service mounts one catch-all under a prefix and the library does the routing.

### The status code carries the outcome

A gateway and a client both read the status before they read a body, so the outcome is in the status.

| Status | Meaning |
| --- | --- |
| 200 | A read, answered |
| 201 | A write reached the board |
| 204 | An acknowledgment was recorded |
| 400 | The body or a query parameter could not be read |
| 404 | No such board, region, notification, or path |
| 405 | That path takes a different method |
| 409 | A premise moved on; the answer names its current version |
| 410 | The run has closed and takes no more writes |
| 422 | The write was refused; the answer names the cause |

409 and 422 are separated rather than sharing 409, because they need different handling and a client should not have to read the body to tell them apart. A version conflict is answered by reading the premise and deciding again. An admission refusal is answered by not sending it again.

410 rather than 409 for a closed run says the run is over rather than the write being wrong, and separates the retry the client should not make from the one it might.

### The library holds the routes both halves use

`blackboard.wire` holds one `Operation` per operation, with its method and its path template. `BoardService` matches on those objects and the agent client builds paths from the same objects, so a rename reaches the two halves together. A service is free to ignore the table and mount its own routes; it still has the bodies.

`BoardService` imports no web framework and opens no socket. It takes a method, a path, and a decoded body, which is what a route in Flask, FastAPI, or Django already holds by the time it calls in.

An operation added to the table that the service does not answer raises rather than falling through to the operation next to it, so the two are found out of step at the first request rather than at an acknowledgment recorded for a read.

## Consequences

A service mounts one route and writes no protocol. An agent team writes no client. Neither half chooses a status code, so neither can change one.

The library now owns a decision it cannot take back cheaply. A path or a status code that changes meaning breaks deployed agents, so both are covered by the same rule as `__all__`: nothing is repurposed, and something is removed only after it has been deprecated with a date.

A service that already has an agent-facing API keeps it. `BoardService` is one way to answer these operations and the bodies work without it.
