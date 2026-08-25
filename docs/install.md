# Installation

```
pip install blackboardx
```

The distribution is named `blackboardx` and the import name is `blackboard`.

```python
from blackboard import create_model
```

It requires Python 3.11 or later. The base install has no runtime dependencies, because the board it ships is backed by SQLite, which comes with Python. The package ships `py.typed`, so type checkers use its annotations without a stub package.

## Extras

A deployment keeps the record in the database it already runs, and the adapter for one needs its driver:

```
pip install 'blackboardx[postgres]'
```

| Extra | Installs | Gives you |
| --- | --- | --- |
| `postgres` | `psycopg[binary,pool]` | `PostgresBoard` |

Naming a board whose extra is not installed raises an `ImportError` saying which extra supplies it. [Storage](concepts/storage.md) covers the choice.

## What the package contains, and what it does not

| Ships | Does not ship |
| --- | --- |
| `SqliteBoard` and `PostgresBoard` | Any transport to remote agents |
| The `BoardStore` protocol, for any other database | Any agent implementation |
| The control component and model creation | Any process supervisor |
| `SystemClock` and `ManualClock` | Any database server, credential, or migration tool |

## Supported versions

The test suite runs on 3.11, 3.12, 3.13 and 3.14 on every change. A version leaving upstream support is removed in a release that says so.
