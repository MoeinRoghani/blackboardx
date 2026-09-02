# Installation

```
pip install blackboardx
```

The distribution is named `blackboardx` and the import name is `blackboard`.

```python
from blackboard import create_model
```

It requires Python 3.11 or later, and the base install has no runtime dependencies. Neither board it ships needs one: `InMemoryStore` holds the record in the process, and `SqliteStore` uses `sqlite3` from the standard library. The package ships `py.typed`, so type checkers use its annotations without a stub package.

## Extras

An extra names a third-party package the base install leaves out.

| Extra | Installs | Gives you |
| --- | --- | --- |
| `postgres` | `psycopg[binary,pool]` | `PostgresStore` |
| `mongodb` | `pymongo` | `MongoStore` |
| `notifier` | `httpx` | `HttpxTransport`, which `HttpNotifier` uses by default |

A deployment keeps the record in a database it already runs, and each adapter needs that database's driver:

```
pip install 'blackboardx[postgres]'
pip install 'blackboardx[mongodb]'
```

Naming a board whose extra is not installed raises an `ImportError` saying which extra supplies it. [Storage](concepts/storage.md) covers the choice.

A blackboard that reaches its agents over HTTP adds the transport:

```
pip install 'blackboardx[postgres,notifier]'
```

[Notifying agents](guides/notifying-agents.md) covers what that gives you. Supplying your own `Transport` needs no extra, because the interface is in the base install and only the `httpx` implementation of it is not.

Several at once is a comma. There is no `all` extra: `postgres` and `mongodb` are alternative drivers for one slot, so a name meaning every driver would invite a deployment to carry one it never uses.

## What the package contains, and what it does not

| Ships | Does not ship |
| --- | --- |
| `SqliteStore`, `PostgresStore`, `MongoStore` | Any HTTP server, and any route it would serve |
| The `BoardStore` protocol, for any other database | Any agent implementation |
| The control component and model creation | Any process supervisor |
| `HttpNotifier`, which sends to agents over HTTP | Any database server, credential, or migration tool |
| The wire bodies both halves encode and decode | Any queue that survives a restart |
| `SystemClock` and `ManualClock` | |

## Supported versions

The test suite runs on 3.11, 3.12, 3.13 and 3.14 on every change. A version leaving upstream support is removed in a release that says so.
