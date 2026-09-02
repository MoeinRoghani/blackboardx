# Storage

A board is a record, and a record has to be somewhere a reader can find it. A **store** is where records are kept, and it holds many boards. Which database backs the store is the application's decision, so `create_model` takes the store as a required argument and supplies no default.

Every store operation names the board it acts on, so one connection serves every board an application runs:

```python
store = PostgresStore(pool)
store.read_premise("incident-4471", "window")
```

## What to pass

| Store | Where records live | Use it for |
| --- | --- | --- |
| `SqliteStore` | A file, or the process when the path is `":memory:"` | One machine: local development, a demonstration, an integration test |
| `PostgresStore` | A Postgres server the application already runs | Deployment |
| `MongoStore` | A MongoDB replica set the application already runs | Deployment |
| `InMemoryStore` | Process memory | A unit test, and nothing else |

`SqliteStore` is in the base distribution, because SQLite ships with Python. The deployment adapters need their drivers, so each is an extra:

```console
pip install 'blackboardx[postgres]'
pip install 'blackboardx[mongodb]'
pip install 'blackboardx[postgres,mongodb]'
```

The third line is the first two together, which pip does with a comma. A deployment wants one database and therefore one extra; both is for testing a board of your own against the conformance suite.

Naming a board whose extra is not installed raises an `ImportError` saying which extra supplies it.

## Local

```python
from blackboard import SqliteStore, create_model

store = SqliteStore("incidents.sqlite3")
model = create_model(board_id="incident-4471", store=store, ...)
```

The file is the record. The schema is created on construction, and reopening the same path reads the record back, sequence counter included; `attach_model` opens a run over it. SQLite ships with Python, so this needs no extra dependency and no server.

## Deployed on Postgres

```python
from psycopg_pool import ConnectionPool

from blackboard import PostgresStore, create_model

# The pool is the application's own, and the adapter neither opens nor closes it.
pool = ConnectionPool("postgresql://...")
store = PostgresStore(pool)

# Once, against a database that has no tables yet.
store.create_schema()

model = create_model(board_id="incident-4471", store=store, ...)
```

Pooling, credentials, failover, and migrations stay where an operator configures them. A script with no pool to pass can open one for the duration of a block:

```python
with PostgresStore.from_dsn("postgresql://...") as store:
    ...
```

`create_schema` creates five tables, all named `blackboard_*` and all `IF NOT EXISTS`, in whatever schema the connection's search path points at. An application that runs its own migrations can issue the same DDL there instead and never call it.

Agents deployed as separate services each hold their own connection to the same database, and the board is what they share. A board adapter makes the record durable; it does not make the run durable, because the control component holds the agent registry, the outstanding notifications, and the deadlines in the process. [Running as a service](service.md) states which part is which and what that means for how many replicas hold one board.

## Deployed on MongoDB

```python
from pymongo import MongoClient

from blackboard import MongoStore, create_model

# The client is the application's own, and the adapter neither opens nor
# closes it.
client = MongoClient("mongodb://...")
store = MongoStore(client["incidents"])

# Once, against a database that has no indexes yet.
store.create_indexes()

model = create_model(board_id="incident-4471", store=store, ...)
```

Content is stored as a document rather than as encoded text, so the record is queryable in the database that was chosen for querying it:

```javascript
db.blackboard_contributions.find({board_id: "incident-4471", "content.finding": "oom"})
```

A script with no client to pass can open one for the duration of a block, as on Postgres:

```python
with MongoStore.from_uri("mongodb://...", "incidents") as store:
    ...
```

`MongoStore` requires a replica set or a sharded cluster. Every write spans two documents, which on MongoDB is a session transaction, and only a replica set runs one. Production MongoDB is a replica set and Atlas is always one. Against a standalone server the first write raises and says why, rather than running the record under weaker rules than it needs.

## What holds across processes

Every board owes two guarantees, and a deployment has to hold them between processes rather than only between the threads of one.

**The sequence is gapless.** Every write takes its number by incrementing one counter inside the transaction that carries the write, so a number a rolled-back write took is returned rather than skipped. A Postgres sequence would be faster and would leave gaps, and a gap is a hole in a record whose numbers are addresses.

The two servers reach that differently. Postgres blocks a second writer on the row lock the increment acquires and holds to commit, so writes to one board serialise. MongoDB does not block: it aborts one of two contending transactions and labels the failure transient, so the adapter runs the write again through the driver's retrying transaction, and a premise write puts its version guard before the counter so a losing write never contends for it at all.

**A premise write is a conditional update on the version.** Two writers naming the same version produce one winner and one `Conflict`, whichever process reaches the record first, and the conflict takes no sequence number.

## Moving a database written by 0.4.0

0.5.0 renames the region kind from `Register` to `Premise`, and the storage identifiers follow. A database a 0.4.0 run wrote holds a table the adapter no longer reads, so it needs one migration before a 0.5.0 run opens against it. A fresh database needs nothing.

Postgres:

```sql
ALTER TABLE blackboard_registers RENAME TO blackboard_premises;
ALTER TABLE blackboard_regions DROP CONSTRAINT blackboard_regions_kind_check;
UPDATE blackboard_regions SET kind = 'premise' WHERE kind = 'register';
ALTER TABLE blackboard_regions
    ADD CONSTRAINT blackboard_regions_kind_check
    CHECK (kind IN ('level', 'premise'));
```

MongoDB:

```javascript
db.blackboard_registers.renameCollection("blackboard_premises")
db.blackboard_regions.updateMany({kind: "register"}, {$set: {kind: "premise"}})
```

SQLite, against the file the run wrote. SQLite enforces a `CHECK` constraint on update and cannot drop one, so the region table is rebuilt rather than updated in place:

```sql
ALTER TABLE registers RENAME TO premises;
CREATE TABLE regions_migrated (
    board_id TEXT NOT NULL,
    name     TEXT NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('level', 'premise')),
    PRIMARY KEY (board_id, name)
);
INSERT INTO regions_migrated (board_id, name, kind)
SELECT board_id, name,
       CASE kind WHEN 'register' THEN 'premise' ELSE kind END
FROM regions;
DROP TABLE regions;
ALTER TABLE regions_migrated RENAME TO regions;
```

## Many boards, one database

Every store call names a board, and every row is scoped by it. Two boards under different identifiers share the tables and see none of each other's writes, sequence numbers included, so one database serving many concurrent runs is the ordinary case rather than a workaround. Moving from a file to a server changes the store that is constructed and nothing else.

## In memory

`InMemoryStore` is a test double. Nothing it holds outlives the process, and two processes running the same code share nothing, so it cannot back a deployment or a run whose result must survive a restart. It exists so a unit test needs no file.

## An adapter of your own

`BoardStore` is the protocol, and it has eight methods. `declare`, `append` and `set` write. `read_level`, `read_premise`, `read_board` and `read_regions` read. `delete` removes one board. Every one names the board it acts on first.

A store records a region's name and its kind and nothing else, so `read_regions` returns a premise without the batch window it was declared with. The window tells the control component when to notify and is no part of the record. An implementation of those eight is a store, and the control component names no concrete type.

Four rules hold every implementation together, and the conformance suite checks each one against all of them, the deployment adapters against real servers:

- **One counter.** Every write to any region takes the next number from a single sequence. The number is the position in the total order and the address of the write.
- **Bounded reads.** Every collection read takes a maximum count, and a reader continues from one past the last sequence it received. A sequence number is the cursor rather than an offset, because an offset shifts when a concurrent write lands.
- **Version-guarded premise writes.** A premise write names the version it expects to replace. If that is not the current version, the write returns `Conflict` carrying the current version, takes no sequence number, and changes nothing.
- **A key writes once.** A write may carry an `idempotency_key`. A key the store has already written answers with what that write produced, marked `repeated`, and adds nothing. A key that named a different region raises `IdempotencyKeyError`. A conflicting premise write stores nothing, so it uses up no key. Keys belong to one board.

The suite ships with the package, so it holds your store to the same cases it holds the library's four to:

```
pip install 'blackboardx[conformance]'
```

```python
import pytest
from blackboard.conformance import BoardConformance, SharedStoreConformance

from myapp.storage import CassandraStore


class TestCassandraStore(BoardConformance):
    @pytest.fixture
    def store(self):
        return CassandraStore(session)


class TestCassandraHoldsManyBoards(SharedStoreConformance):
    @pytest.fixture
    def store(self):
        return CassandraStore(session)
```

The four stores the library ships are held to that module rather than to a copy of it, so what you run is what the library runs. Reading the rules above and reimplementing them is not the same thing: a store that gives each region its own counter passes a reading of the prose and fails six cases here.

Content crosses the protocol as JSON, because a deployed board crosses a process boundary. A tuple written comes back a list, and content JSON cannot carry raises `TypeError` before anything is stored. Every implementation behaves this way, including the in-memory one, so a test cannot pass against content a deployment would refuse.

## Writing once, over a network that may repeat

A write that crosses a network can be sent twice. The client sends it, the connection drops before the answer arrives, and the client cannot tell whether the board took it. Sending it again would append the contribution twice, and a level holding one finding twice is not the same board: an agent counting them counts wrong, and an admission rule reading them decides wrong.

An idempotency key fixes that where it can be fixed. The caller names the write, and the store writes it once:

```python
outcome = store.append(board_id, "findings", {"cause": "a bad deploy"}, "write-8f21")
if outcome.repeated:
    ...  # an earlier attempt had already landed; this one added nothing
```

The key is stored on the contribution row rather than beside it. One insert writes the row and its key together, so they cannot disagree, and the uniqueness of the key is the database's to enforce rather than the adapter's to remember. That is what makes two processes writing under one key safe.

A key names one write on one board. The same key on another board is another write, because two runs share nothing. A key sent for a region it did not name before raises `IdempotencyKeyError`, since that is a mistake rather than a retry. A key sent again for the region it did name returns the first write whatever content comes with it, so a retry sends what it sent before.

`SqliteStore` and `PostgresStore` add the columns to a table written by an earlier version rather than assuming them, and `MongoStore` indexes the key only where one is present, so documents written before keys existed do not collide.

## What wrote this record

Each of the three stores that keep a record on disk stamps it with a schema number and checks that number when it opens. A record written for a schema this version cannot read is refused there, with a sentence naming both numbers, rather than at whichever query first touches the piece that changed.

```
SchemaVersionError: this database holds a record written for schema 2, and
this version of blackboardx reads 1. Upgrade blackboardx to a version that
reads it.
```

The number counts changes to the physical schema rather than releases. Most releases change nothing, and a check that fires on every release is a check nobody can leave on. `blackboard.SCHEMA_VERSION` is what this version reads.

`SqliteStore` checks when the file opens. `PostgresStore` and `MongoStore` check before their first operation as well as when `create_schema` or `create_indexes` runs, because an application pointed at a database it did not create never calls those.

A record written before stamps existed carries none. It is stamped rather than refused, since everything the earlier versions wrote is readable by this one.

The library never stamps a record backwards. An older version would then read fields a newer one wrote and take them at face value, which is the failure the stamp exists to prevent.

## Removing a board

A store holds many boards, and `delete` removes one:

```python
gone = store.delete("incident-4471")
log.info("removed %d writes across %d regions", gone.writes, gone.regions)
```

Everything or nothing. The regions, the record, the premise values, and the sequence counter go together in one transaction, so a board that comes back is a board that is gone rather than one that is half there. The same identifier declared again starts its sequence at 1, and the idempotency keys the old board wrote are free.

A board the store never held names nothing rather than failing, so a delete that runs twice is safe and a retention job needs no lookup first.

**Nothing in the library calls it.** Deleting is a retention decision and the control component makes none: a run that closes deletes no board, and no board expires. The application decides.

**Close the run first.** A `Control` still serving the board goes on writing to a record that is no longer there, and the store cannot see that it is running. `control.outcome()` returning something other than `None` is how you know a run has closed.

## Reading a board in pieces

A read is bounded below by a sequence number and above by a count:

```python
cursor = 0
while True:
    page = store.read_level(board_id, "platform", from_sequence=cursor, limit=100)
    if not page:
        break
    handle(page)
    cursor = page[-1].sequence + 1
```

The sequence number is the cursor. An offset would shift when a concurrent write landed between two reads, and a sequence number does not, so a reader never skips a contribution or sees one twice.

`read_regions` names what a board holds, which a process that did not create the board needs before it can read anything:

```python
for region in store.read_regions(board_id):
    print(region.name, type(region).__name__)
```
