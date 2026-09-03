# Installation

```
pip install blackboardx
```

The distribution is named `blackboardx` and the import name is `blackboard`.

```python
from blackboard import create_model
```

`blackboardx` requires Python 3.11 or later, and the base install has no runtime dependencies. `InMemoryStore` holds the record in the process, and `SqliteStore` uses `sqlite3` from the standard library. The package ships `py.typed`, so type checkers use its annotations without a stub package.

## Extras

An extra names a third-party package the base install leaves out.

| Extra | Installs | Gives you |
| --- | --- | --- |
| `postgres` | `psycopg[binary,pool]` | `PostgresStore` |
| `mongodb` | `pymongo` | `MongoStore` |
| `notifier` | `httpx` | `HttpxTransport`, which `HttpNotifier` uses by default |
| `agent` | `httpx` | `BoardClient` and `AsyncBoardClient`, for an agent calling a blackboard |
| `conformance` | `pytest` | `blackboard.conformance`, for holding a store of your own to the suite |

A deployment keeps the record in a database it already runs, and each adapter needs that database's driver:

```
pip install 'blackboardx[postgres]'
pip install 'blackboardx[mongodb]'
```

Naming a store whose extra is not installed raises an `ImportError` saying which extra supplies it: `MongoStore needs the 'mongodb' extra: pip install 'blackboardx[mongodb]'`. [Storage](concepts/storage.md) covers the choice of store.

A blackboard that reaches its agents over HTTP adds the transport:

```
pip install 'blackboardx[postgres,notifier]'
```

[Notifying agents](guides/notifying-agents.md) covers what that gives you. Supplying your own `Transport` needs no extra, because the interface is in the base install, and only the `httpx` implementation of it is left out.

An agent calling a blackboard installs the `agent` extra, and nothing else:

```
pip install 'blackboardx[agent]'
```

That pulls no database driver. An agent reads and writes through the blackboard, never through its database, so it has no use for a driver. [Write an agent](guides/writing-an-agent.md) covers the client.

Answering an agent's requests needs no extra at all. `blackboard.server` depends on nothing and is in the base install.

Writing a store of your own adds the suite it is held to:

```
pip install 'blackboardx[conformance]'
```

[An adapter of your own](concepts/storage.md#an-adapter-of-your-own) covers what to subclass.

Several extras at once are separated by a comma. There is no `all` extra: `postgres` and `mongodb` are alternative drivers for the same role, so a name meaning every driver would invite a deployment to carry one it never uses.

## What the package contains, and what it does not

| Ships | Does not ship |
| --- | --- |
| `InMemoryStore`, `SqliteStore`, `PostgresStore`, `MongoStore` | Any HTTP server, and any route it would serve |
| The `BoardStore` protocol, for any other database | Any agent implementation |
| The control component, and `create_model` and `attach_model` | Any process supervisor |
| `HttpNotifier`, which sends to agents over HTTP | Any database server, credential, or migration tool |
| `BoardService`, which answers an agent's requests | Any authentication or authorisation |
| `BoardClient` and `AsyncBoardClient`, which an agent calls a blackboard with | |
| The conformance suite, for a store of your own | |
| The wire bodies that both halves encode and decode | Any queue that survives a restart |
| `SystemClock` and `ManualClock` | |

## Supported versions

The test suite runs on 3.11, 3.12, 3.13 and 3.14 on every change. A version leaving upstream support is removed in a release that says so.
