# The board

The board holds what the agents write, gives every write a position in a single order, and hands any of it back to any reader.

The board never opens what it holds. A contribution's content belongs to the application that wrote it, and reading that content takes the expertise of the agent that produced it, and the board does not have that expertise.

## The two kinds of region

A board is divided into named regions, which the application declares. Every region is one of two kinds, because the information on a board is of two kinds: what the work was given, and what the agents worked out from it.

A **level** holds what the agents worked out. Each thing an agent writes to a level is a contribution: a finding, a hypothesis, a bundle of evidence it gathered. One agent's finding does not make another's untrue, so contributions do not supersede one another, and a write to a level adds to what is already there without altering any of it.

A **premise** holds something the work was given: which service is affected, which window of time is under examination, which namespaces are in scope. There is one correct answer at a time, so a premise holds one current value, and a write to it replaces that value. The writer names the version it expects to replace, because the value it read may have moved in the meantime, and the board refuses the write when the value has moved.

| Kind | Holds | In an incident investigation |
| --- | --- | --- |
| `Level` | What the agents worked out | The findings each agent contributed |
| `Premise` | What the work was given | The affected service, the incident window |

A region's kind carries its reconciliation rule, so no separate setting exists through which the kind and the rule could disagree.

## Changing part of what a premise holds

A premise write replaces the whole value. Writing `{"window": ...}` to a premise that holds `{"service": ..., "window": None}` leaves it holding the window alone, and the service key is gone.

The board cannot fill in one field and leave the others, because working out which fields are already set means reading the value, and opening a value is the one thing it does not do. The same constraint rules out combining two writes into one value.

An application does that reading itself.

```python
state = board.read_premise("case")
board.set_premise("case", {**state.value, "window": "20:00-22:00"}, state.version)
```

The writer reads the current value, builds the whole new value from it, and writes that under the version it read. Two writers filling in different fields at the same moment lose nothing: the second is told the value moved, reads the newer one, and builds again from that.

## One order across everything

Every write to any region takes the next number from one counter. Per-region counters would not serve, because an agent reading two regions must order a contribution in one region against a write in the other region, and two independent counters cannot express that relation.

The sequence number is both a position and an address. Nothing else identifies a write, because that number already picks out exactly one.

Sequence assignment is the only point at which two writes wait on each other.

## Who wrote it, and when

Every write records the name it carried and the instant the store stamped, so the record answers both questions without a second table to consult.

```python
contribution.writer  # the name the write named, or None
contribution.written_at  # the store's clock when it landed
```

The instant comes from the store rather than from the caller. Agents run as separate services, and two of them disagreeing about the time would make the record unanswerable on a question the sequence cannot settle: the sequence orders writes against each other, and the instant says when in the day one happened.

An opening premise value names no writer, because the application supplied it before any agent registered. A record written by an earlier version of this library carries no name and no instant, and both read as `None`.

## Reads

Any caller may read anything, at any time. Reads bypass the control component entirely, so they consume no capacity and cannot be refused.

```python
board.read_regions()  # what is declared, and of which kind
board.read_level("platform", from_sequence=4)  # that level, from a bound
board.read_premise("window")  # value and version
board.read_board(from_sequence=4)  # every region, in order
```

A read needs the stored record and does not need an open run. `reader_for` binds those four reads to one board of a store, and they answer from the record with a run open over it or without one.

```python
board = reader_for(store, "incident-3391")
```

Reads return snapshots, so mutating the returned list changes nothing. Content crosses the board as JSON and comes back detached from what the caller wrote, so mutating a stored object afterwards changes nothing a later reader sees. A tuple that is written comes back as a list, and content that JSON cannot carry raises `TypeError` before anything is stored.

## Substituting the board

`Control` takes a `BoardStore`, the protocol covering the seven operations `Control` performs. The eighth method, `delete`, is the application's to call, and the control component never calls it. There is no default: a run has to be told where its record goes.

`SqliteStore` keeps the record in a file, and an adapter you write keeps it in your own database. The two reconciliation rules map onto ordinary primitives: the total order is a sequence, and a premise write is an update guarded by a version. [Storage](storage.md) covers the choice and what an implementation has to guarantee.
