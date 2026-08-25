# The board

The board holds what the agents write, gives every write a position in a single order, and hands any of it back to any reader.

It never opens what it holds. A contribution's content belongs to the application that wrote it, and reading that content takes the expertise of the agent that produced it, which the board does not have.

## Why there are two kinds of region

Suppose two agents write to the same region at the same moment. The board has to decide what the region holds afterwards, and in principle three answers are available: keep both writes, keep one of them, or combine them into a single value.

Combining is not available to the board. Working out the combination of two values means reading both of them first, and reading is the thing the board does not do. A merge function the application supplies does not rescue it, because the board would still have to open what it holds in order to pass it to that function.

Keeping both and keeping one remain, and each needs one further rule before it works.

Keeping both needs an order, so that a reader sees the two writes in the sequence they arrived rather than in an arbitrary one.

Keeping one needs a way for a writer to learn which value it replaced. Without that, a writer that read a value, computed a longer one from it, and wrote the result would silently discard whatever another writer had put there in the meantime.

The two remaining answers, with those rules attached, are the two kinds of region. There is no third, because the third answer was combining.

| Kind | What it does with two writes | What it holds |
| --- | --- | --- |
| `Level` | Keeps both, in arrival order | What the agents concluded |
| `Premise` | Keeps one, and the writer names the version it expects to replace | What the work was given |

The kind carries the rule, so no second setting exists through which kind and rule could disagree.

## Levels accumulate

Appending to a level never conflicts and never needs retrying. Two agents appending at the same moment both succeed and receive different sequence numbers.

```python
board.append("platform", {"findings": ["oom"]})  # -> 4
```

Nothing already stored is altered, so a contribution an agent read is still there and still says what it said.

## Registers replace under a version

A premise write names the version it expects to replace, and fails if the premise moved past it.

```python
state = board.read_premise("namespace")
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
board.read_premise("window")  # value and version
board.read_board(from_sequence=4)  # every region, in order
```

Reads return snapshots, so mutating the returned list changes nothing. Content crosses the board as JSON and comes back detached from what the caller wrote, so mutating a stored object afterwards changes nothing a later reader sees. A tuple written comes back a list, and content JSON cannot carry raises `TypeError` before anything is stored.

## Substituting the board

`Control` takes a `BoardStore`, the protocol covering the six operations it performs. There is no default: a run has to be told where its record goes.

`SqliteBoard` keeps it in a file, and an adapter you write keeps it in your own database. The two reconciliation rules map onto ordinary primitives: the total order is a sequence, and a premise write is an update guarded by a version. [Storage](storage.md) covers the choice and what an implementation has to guarantee.
