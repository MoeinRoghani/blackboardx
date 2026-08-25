# Installation

```
pip install blackboardx
```

The distribution is named `blackboardx` and the import name is `blackboard`.

```python
from blackboard import create_model
```

It requires Python 3.11 or later. The base install has no runtime dependencies, because the board it ships is backed by SQLite, which comes with Python. The package ships `py.typed`, so type checkers use its annotations without a stub package.

## What the package contains, and what it does not

| Ships | Does not ship |
| --- | --- |
| `SqliteBoard`, for one machine | Any transport to remote agents |
| The `BoardStore` protocol, for anything else | Any agent implementation |
| The control component and model creation | Any process supervisor |
| `SystemClock` and `ManualClock` | |

Keeping the record in your own database means implementing `BoardStore` against it, which is six methods. [Storage](concepts/storage.md) describes what each has to guarantee.

## Supported versions

The test suite runs on 3.11, 3.12, 3.13 and 3.14 on every change. A version leaving upstream support is removed in a release that says so.
