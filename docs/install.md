# Installation

```
pip install blackboardx
```

The distribution is named `blackboardx` and the import name is `blackboard`.

```python
from blackboard import create_model
```

It requires Python 3.11 or later and has no runtime dependencies. The package ships `py.typed`, so type checkers use its annotations without a stub package.

## What the package contains, and what it does not

There are no optional extras. One install gives you everything the distribution has.

| Ships | Does not ship |
| --- | --- |
| The board, the control component, model creation | Any database adapter |
| The `BoardStore` protocol | Any implementation of it besides the in-memory board |
| `SystemClock` and `ManualClock` | Any transport to remote agents |

Keeping the record somewhere other than process memory means implementing `BoardStore` against your own database, which is six methods. [The board](concepts/board.md) describes what each has to guarantee.

## Supported versions

The test suite runs on 3.11, 3.12, 3.13 and 3.14 on every change. A version leaving upstream support is removed in a release that says so.
