# Installation

```
pip install blackboardx
```

The distribution is named `blackboardx` and the import name is `blackboard`.

```python
from blackboard import create_model
```

It requires Python 3.11 or later, and the base install has no runtime dependencies. Neither board it ships needs one: `InMemoryBoard` holds the record in the process, and `SqliteBoard` uses `sqlite3` from the standard library. The package ships `py.typed`, so type checkers use its annotations without a stub package.

## Extras

A deployment keeps the record in a database it already runs, and each adapter needs that database's driver:

```
pip install 'blackboardx[postgres]'
pip install 'blackboardx[mongodb]'
```

| Extra | Installs | Gives you |
| --- | --- | --- |
| `postgres` | `psycopg[binary,pool]` | `PostgresBoard` |
| `mongodb` | `pymongo` | `MongoBoard` |

Naming a board whose extra is not installed raises an `ImportError` saying which extra supplies it. [Storage](concepts/storage.md) covers the choice.

Both at once is a comma, and needs no extra of its own:

```
pip install 'blackboardx[postgres,mongodb]'
```

A deployment keeps its record in one database, so it wants one of them. Ask for both when you are testing a board of your own against the conformance suite, which runs against both servers, or when one service creates runs on either.

There is no `all` extra. These two are alternative drivers for one slot rather than features that stack, so a name meaning every driver would invite a deployment to carry one it never uses.

## What the package contains, and what it does not

| Ships | Does not ship |
| --- | --- |
| `SqliteBoard`, `PostgresBoard`, `MongoBoard` | Any transport to remote agents |
| The `BoardStore` protocol, for any other database | Any agent implementation |
| The control component and model creation | Any process supervisor |
| `SystemClock` and `ManualClock` | Any database server, credential, or migration tool |

## Supported versions

The test suite runs on 3.11, 3.12, 3.13 and 3.14 on every change. A version leaving upstream support is removed in a release that says so.
