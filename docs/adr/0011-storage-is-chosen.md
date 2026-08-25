# ADR 0011: Storage is chosen, never defaulted

Date: 2026-08-25

## Status

Accepted. Supersedes the specification's silence about where the record is kept.

## Context

The library shipped one board, held in a Python dictionary, and `create_model` used it when no other was passed. The `BoardStore` protocol existed, so an application could supply its own implementation, and none was supplied with the library.

This made the in-memory board the default for every application that did not know to replace it. Nothing it holds outlives the process, and two processes running the same code share nothing. A deployment whose agents are separately deployed services has no shared record at all under that default: each replica holds its own board, an agent reaching one replica cannot see what an agent reaching another wrote, and a restart loses the run.

A default cannot be right here. Whichever it is, it is wrong for the other case, and the wrong one fails by holding the record somewhere a second reader cannot reach, which is invisible until a second reader exists.

## Decision

`board` is a required argument to `create_model` and to `Control`. There is no default.

Three implementations ship, and every one of them is a database.

| Board | Where the record lives | For |
| --- | --- | --- |
| `SqliteBoard` | A file, or the process when the path is `":memory:"` | One machine |
| `PostgresBoard` | A Postgres server the application runs | Deployment |
| `MongoBoard` | A MongoDB replica set the application runs | Deployment |

`InMemoryBoard` is what the old `Board` is now called, and it is documented as a test double.

An adapter is handed the connection pool or database handle the application already configures, and neither opens nor closes it. The library owns no server, no credential, and no migration tool.

Every persistent board carries a `board_id`, and every row or document is scoped by it, so one database holds many concurrent runs.

Content crosses every implementation as JSON, the in-memory board included, so a test cannot pass against content a deployment would refuse.

The deployment drivers are extras, `blackboardx[postgres]` and `blackboardx[mongodb]`, so the base install still has no runtime dependency.

## Consequences

- Every caller states where its record goes, and no application reaches deployment holding its record in process memory because an argument was omitted.
- `Board` is renamed and `board` is required, which is a breaking change in both directions: the name and the signature.
- A tuple written to any board reads back as a list, and content JSON cannot carry raises `TypeError` at the write rather than at the first process boundary.
- A conformance suite defines what an implementation owes, and the deployment adapters are held to it against real servers in CI. A skip in that job fails it, because a skip means a server did not come up.
- `MongoBoard` requires a replica set, because both guarantees span documents and that is a session transaction. A standalone server raises and says so.

## Alternatives rejected

- **Keeping the in-memory default.** It is wrong for every deployment and fails silently, which is the worst way to be wrong.
- **Shipping only the protocol.** Six methods against a database is a day of work per application, done identically each time, and done wrong in the two places that are not obvious: the gapless sequence and the version guard.
- **A Postgres sequence for the total order.** Faster, and it leaves gaps on rollback. A gap is a hole in a record whose numbers are addresses.
- **Falling back to non-transactional writes on a standalone MongoDB.** It would run the record under weaker rules than it needs, and say nothing.
