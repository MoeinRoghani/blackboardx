# Storage

The board is a record, and a record has to be somewhere a reader can find it. Which database holds it is the application's decision, so `create_model` takes the board as a required argument and the library defaults to nothing.

## What to pass

| Board | Where the record lives | Use it for |
| --- | --- | --- |
| `SqliteBoard` | A file, or the process when the path is `":memory:"` | One machine: local development, a demonstration, an integration test |
| `PostgresBoard` | A Postgres server the application already runs | Deployment |
| `InMemoryBoard` | Process memory | A unit test, and nothing else |

`SqliteBoard` is in the base distribution, because SQLite ships with Python. `PostgresBoard` needs its driver, so it is an extra:

```console
pip install 'blackboardx[postgres]'
```

Naming it without the extra installed raises an `ImportError` saying which extra supplies it.

## Local

```python
from blackboard import SqliteBoard, create_model

model = create_model(..., board=SqliteBoard("incident.sqlite3"))
```

The file is the record. The schema is created on construction, and reopening the same path reads the run back, sequence counter included. SQLite ships with Python, so this needs no extra dependency and no server.

## Deployed

```python
from psycopg_pool import ConnectionPool

from blackboard import PostgresBoard, create_model

# The pool is the application's own, and the adapter neither opens nor closes it.
pool = ConnectionPool("postgresql://...")
board = PostgresBoard(pool, board_id="incident-4471")

# Once, against a database that has no tables yet.
board.create_schema()

model = create_model(..., board=board)
```

The adapter is handed the pool and neither opens nor closes it, so pooling, credentials, failover, and migrations stay where an operator configures them. A script with no pool to pass can open one for the duration of a block:

```python
with PostgresBoard.from_dsn("postgresql://...", board_id="incident-4471") as board:
    ...
```

`create_schema` creates four tables, all named `blackboard_*` and all `IF NOT EXISTS`, in whatever schema the connection's search path points at. An application that runs its own migrations can issue the same DDL there instead and never call it.

Agents deployed as separate services each hold their own connection to the same database. The board is what they share, and the process that created the model holds nothing another process needs, so any pod serves any board and losing a pod loses no work.

### What holds across processes

Two guarantees are what make a board a board, and in a deployment they have to hold between processes rather than merely between the threads of one.

**The sequence is gapless.** Every write increments one row and holds the lock that update takes until the transaction commits, so writes to one board are serialised and a number a rolled-back write took is returned rather than skipped. A Postgres sequence would be faster and would leave gaps, and a gap is a hole in a record whose numbers are addresses.

**A register write is a conditional update on the version.** Two writers naming the same version produce one winner and one `Conflict`, whichever process reaches the row first, and the conflict takes no sequence number.

## Many boards, one database

Every persistent board carries a `board_id`, and every row is scoped by it. Two boards under different identifiers share the tables and see none of each other's writes, sequence numbers included, so one database serving many concurrent runs is the ordinary case rather than a workaround. `SqliteBoard` takes the same argument, so moving from a file to a server changes the board that is constructed and nothing else.

## In memory

`InMemoryBoard` is a test double. Nothing it holds outlives the process, and two processes running the same code share nothing, so it cannot back a deployment or a run whose result must survive a restart. It exists so a unit test needs no file.

## An adapter of your own

`BoardStore` is the protocol, and it has six methods: `declare`, `append`, and `set` write; `read_level`, `read_register`, and `read_board` read. An implementation of those six is a board, and the control component names no concrete type.

Two rules hold every implementation together, and the conformance suite in `tests/conformance.py` checks both against each one, `PostgresBoard` against a real server:

- **One counter.** Every write to any region takes the next number from a single sequence. The number is the position in the total order and the address of the write.
- **Version-guarded register writes.** A register write names the version it expects to replace. If that is not the current version, the write returns `Conflict` carrying the current version, takes no sequence number, and changes nothing.

Content crosses the protocol as JSON, because a deployed board crosses a process boundary. A tuple written comes back a list, and content JSON cannot carry raises `TypeError` before anything is stored. Every implementation behaves this way, including the in-memory one, so a test cannot pass against content a deployment would refuse.
