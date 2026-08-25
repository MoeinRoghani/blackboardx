# Storage

The board is a record, and a record has to be somewhere a reader can find it. Which database holds it is the application's decision, so `create_model` takes the board as a required argument and the library defaults to nothing.

## What to pass

| Board | Where the record lives | Use it for |
| --- | --- | --- |
| `SqliteBoard` | A file, or the process when the path is `":memory:"` | One machine: local development, a demonstration, an integration test |
| An adapter you supply | Whatever database your application runs | Deployment |
| `InMemoryBoard` | Process memory | A unit test, and nothing else |

## Local

```python
from blackboard import SqliteBoard, create_model

model = create_model(..., board=SqliteBoard("incident.sqlite3"))
```

The file is the record. The schema is created on construction, and reopening the same path reads the run back, sequence counter included. SQLite ships with Python, so this needs no extra dependency and no server.

## In memory

`InMemoryBoard` is a test double. Nothing it holds outlives the process, and two processes running the same code share nothing, so it cannot back a deployment or a run whose result must survive a restart. It exists so a unit test needs no file.

## An adapter of your own

`BoardStore` is the protocol, and it has six methods: `declare`, `append`, and `set` write; `read_level`, `read_register`, and `read_board` read. An implementation of those six is a board, and the control component names no concrete type.

An adapter is handed a connection the application already owns, so pooling, credentials, failover, and migrations stay where the application configures them. Agents deployed as separate services each hold their own connection to the same database; the board is what they share, and the process that created the model holds nothing another process needs.

Two rules hold every implementation together, and the conformance suite in `tests/conformance.py` checks both against each one:

- **One counter.** Every write to any region takes the next number from a single sequence. The number is the position in the total order and the address of the write.
- **Version-guarded register writes.** A register write names the version it expects to replace. If that is not the current version, the write returns `Conflict` carrying the current version, takes no sequence number, and changes nothing.

Content crosses the protocol as JSON, because a deployed board crosses a process boundary. A tuple written comes back a list, and content JSON cannot carry raises `TypeError` before anything is stored. Every implementation behaves this way, including the in-memory one, so a test cannot pass against content a deployment would refuse.
