# The board

The board stores contributions and decides nothing about them. It never reads what it stores, and that single limit determines everything else about it.

## Why there are two kinds of region

Reconciling two writes to one place means either keeping both or keeping one. Combining them would require reading both, and the board never reads. So there are two kinds of region and there cannot be a third.

| Kind | Reconciliation | Holds |
| --- | --- | --- |
| `Level` | Keeps both, in arrival order | What the agents concluded |
| `Register` | Keeps one, guarded by a version | A premise of the case |

A merge function supplied by the application, or a counter the board sums, would both require the board to open what it holds. The kind carries the rule, so no second setting exists through which kind and rule could disagree.

## Levels accumulate

Appending to a level never conflicts and never needs retrying. Two agents appending at the same moment both succeed and receive different sequence numbers.

```python
board.append("platform", {"findings": ["oom"]})  # -> 4
```

Nothing already stored is altered, so a contribution an agent read is still there and still says what it said.

## Registers replace under a version

A register write names the version it expects to replace, and fails if the register moved past it.

```python
state = board.read_register("namespace")
result = board.set("namespace", [*state.value, "prod-payments"], state.version)
```

`result` is `Written` with the new version, or `Conflict` carrying the version now current. The board neither retries nor merges; the writer re-reads and decides again.

That is how a value that grows is grown. Two writers adding at once lose nothing: the second is told the value moved, reads the newer one, and adds to that.

## One order across everything

Every write to any region takes the next number from one counter. Per-region counters would not serve, because an agent reading two regions must place a contribution in one against a write in the other, and two independent counters cannot express that relation.

The sequence number is both a position and an address. Nothing else identifies a write, because that number already names one and no other.

Sequence assignment is the only point at which two writes wait on each other.

## Reads

Any caller may read anything, at any time. Reads bypass the control component entirely, so they consume no capacity and cannot be refused.

```python
board.read_level("platform", from_sequence=4)  # that level, from a bound
board.read_register("window")  # value and version
board.read_board(from_sequence=4)  # every region, in order
```

Reads return snapshots, so mutating the returned list changes nothing. Content crosses the board as JSON and comes back detached from what the caller wrote, so mutating a stored object afterwards changes nothing a later reader sees. A tuple written comes back a list, and content JSON cannot carry raises `TypeError` before anything is stored.

## Substituting the board

`Control` takes a `BoardStore`, the protocol covering the six operations it performs. There is no default: a run has to be told where its record goes.

`SqliteBoard` keeps it in a file, and an adapter you write keeps it in your own database. The two reconciliation rules map onto ordinary primitives: the total order is a sequence, and a register write is an update guarded by a version. [Storage](storage.md) covers the choice and what an implementation has to guarantee.
