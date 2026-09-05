# Migrating

Every name that moved, what replaced it, and the release its old form stops
working in.

## Deprecated, and their removal dates

| Name | Replaced by | May be removed on or after |
| --- | --- | --- |
| `attach_model` | `create_model`, which opens a board once; every later operation reads what it needs from the store | 2026-12-05 |
| `Control.read_audit` | A contribution's `writer` and `written_at` for who and when; the `blackboard` logger for the rest | 2026-12-05 |

Both exist because the run lived in one process's memory. `attach_model` opened
a run over a record whose run had died with its process, and `read_audit`
returned a history that died the same way. Both answered questions the record now answers itself.

Both keep working until the date above, and each warns at run time naming its
replacement.

## 0.11 to 0.12

### A write records the intent to notify

`append` and `set` take `notify`, a set of agent names, and record one row for
each in the same transaction as the contribution. `unsent` answers with the
rows nothing has sent, oldest first; `mark_sent` removes one.

A store of your own implements both and records `notify` on both writes.
`blackboard.conformance.OutboxConformance` decides if you got it right.

Marking after sending rather than before is what makes delivery at least once.
A process that sends and stops before marking sends again, and the repeat is
absorbed by cumulative acknowledgment.


One thing changed, and only for anyone who wrote a store: `BoardStore` holds
how far each agent has been notified and has answered. An application using a
store shipped here has nothing to do.

### The store holds how far each agent has got

How far an agent had been told lived in the process that told it. A process
that takes a write is not always the process an agent registered with, so that
number belonged to neither of them.

`BoardStore` went from thirteen methods to sixteen, and no default stands in
for the three. `mark_notified` records that an agent has been told everything
through a sequence number. `acknowledge` records how far it has answered and
returns the entry as it stood before the call, which is how a first
acknowledgment is told from a repeat without a second read. `read_agents`
answers with both numbers for every agent the board has notified.

Both numbers are sequence numbers on the board and both only rise. Two
processes notifying one agent leave the higher of what they wrote, whichever
order they arrive in, so neither undoes the other and no lock orders them.
`acknowledge` is the one place a compare-and-set is load-bearing: of several
callers naming one sequence, exactly one is answered with an entry below it.

`blackboard.conformance.AgentConformance` decides if you got them right, and
holds a store to the concurrent cases as well as the ordinary ones.

The schema number rises to 3.

### A notification is identified by the range it covers

The identifier was a counter starting at one in each process. It is now the
sequence the notification's range ends at, because a counter in one process
cannot be allocated from another and the cumulative rule compared nothing
else.

Three things follow, and each is visible without a store of your own.

Two agents told of one write now carry one identifier. It names the work
rather than the delivery, and each agent answers for itself. Code asserting
that identifiers increase by one per delivery is asserting the old counter.

Identifiers no longer start at one on a new run over an existing board. They
continue from the board's sequence, which is what makes them agree across
processes.

An acknowledgment naming a sequence in the range an agent was told, but not
one exactly issued to it, is now accepted as a claim of progress rather than
refused. One outside that range still raises `UnknownNotificationError`.

### A cursor survives the process

How far an agent answered is on the record, so an agent registering against a
process that never saw it resumes from there instead of being told the whole
board again. Work it had finished and acknowledged is not done twice.

An agent that had answered everything is now registered without being woken.
It was previously handed a notification whose range started after it ended.

### `notify_due` is how a write reaches an agent elsewhere

A process serving writes for a board whose agents registered with another
process reaches them through `Control.notify_due`. It reads what has landed
since each of its own agents last answered and delivers it, and it names the
agents it woke. Schedule it beside `close_expired`.

A deployment running one process per board does not need it. That process
notifies on the write path, so the poll finds nothing.

### `create_model` converges on a board that exists

It raised `DuplicateRegionError` on a board the store already held. It now
records a region the record holds with the same name and kind rather than
writing it again, so every replica runs the same call and none has to know
whether it is the first.

A name held as the other kind still raises `RegionKindError`. An opening
premise value is written only where the record holds none, because the
record holds the value and the version it is at.

Code that relied on the refusal to tell an existing board from a new one
should read the board instead: `store.read_regions` answers with what is
declared, and an empty list means the board is new.

`attach_model` stays deprecated and its message now names a replacement that
works. Where it was called on a board that exists, `create_model` with the
same regions does the same thing.

### `close_expired` names the agents that did not finish

It passed an empty set, because a sweeper holds no declarations and could not
work out who was owed an answer. Both numbers are on the record now, so a run
closed by the sweep names the same unfinished agents a run closed in process
does.

## 0.10 to 0.11

Two things changed for anyone who wrote a store: a write names its writer, and
the run moved onto `BoardStore`. Everything else in the release adds to the
public surface without moving anything, and an application using a store
shipped here has nothing to do.

### A write names its writer

`append` and `set` take a `writer` after the idempotency key, and the store
records it beside an instant stamped by its own clock. Both read back on
`Contribution`, `PremiseState` and `BoardChange`, and both read as `None` on a
record an earlier version wrote.

A store that takes no such parameter raises `TypeError` at the first write,
because the control component passes it by keyword.

### A store carries the run

The run's two deadlines and its outcome were held in `Control`, in the memory
of the process that opened the run. They are rows now, so any process holding
the store reads how long a board has been quiet and closes it.

`BoardStore` went from eight methods to thirteen, and no default stands in for
the five. They are `open_run`, `read_run`, `touch_run`, `close_run` and
`runs_past_deadline`. `blackboard.conformance.RunConformance` decides if you
got them right, and the rule they turn on is `close_run`: it answers `True` to
the one caller that recorded the outcome and `False` to every other. A caller
that is answered `False` reads the outcome the winner wrote and reports that
one instead of its own, so two processes reaching a deadline together name the
same outcome rather than each naming its own. A store that answers `True`
twice loses that agreement.

No instant crosses this part of the protocol. `open_run` and `touch_run` take
their limits in seconds and compute the deadlines against the store's own
clock, and `read_run` returns that clock beside them, so a caller compares two
instants that came from one place. Agents run as separate services and their
clocks disagree.

These changes cannot be deprecated. A protocol is satisfied or it is not, and
a method that is absent is absent at the first call rather than at a warning.

## 0.7 to 0.8

Two things changed for anyone who wrote a store: a store holds many boards, and
a write carries a key. Everything else in the release adds to the public
surface without moving anything.

### A store holds many boards

A board object no longer stands for one run.

```python
# 0.7
board = PostgresBoard(pool, board_id="incident-4471")
model = create_model(regions=[...], premises={...}, limits=..., board=board)
board.read_premise("window")
```

```python
# 0.8
store = PostgresStore(pool)
model = create_model(
    board_id="incident-4471",
    store=store,
    regions=[...],
    premises={...},
    limits=...,
)
store.read_premise("incident-4471", "window")
```

| Was | Is |
| --- | --- |
| `InMemoryBoard`, `SqliteBoard`, `PostgresBoard`, `MongoBoard` | `InMemoryStore`, `SqliteStore`, `PostgresStore`, `MongoStore` |
| `PostgresBoard(pool, board_id=x)` | `PostgresStore(pool)`, and every call names the board |
| `create_model(board=...)` | `create_model(board_id=..., store=...)` |
| `store.append(level, content)` and the other five | each takes the board id first |
| `board_id` defaulting to `"default"` | no default; the caller names the board on every call |

`model.reader` is unchanged. It is bound to its own board, so
`read_premise("window")` still takes only the region.

`Notification` gains `board_id`, so an agent serving several boards can tell
which one woke it.

### A write carries a key

`BoardStore.append` answers with `Written` rather than a sequence number, so it
can say a write was one that this key had already made.

```python
# 0.7
sequence = store.append("incident-4471", "findings", {"cause": "a bad deploy"})
```

```python
# 0.8
written = store.append("incident-4471", "findings", {"cause": "a bad deploy"})
written.sequence
```

| Was | Is |
| --- | --- |
| `store.append(board_id, level, content) -> int` | `store.append(board_id, level, content, idempotency_key=None) -> Written` |
| `store.set(board_id, premise, value, expected_version)` | the same, with `idempotency_key=None` after it |
| six methods on `BoardStore` | eight; `read_regions` names them and `delete` removes one board |

Only a store of your own needs work. Add the parameter to `append` and to
`set`, return `Written` from `append`, and read [an adapter of your
own](concepts/storage.md#an-adapter-of-your-own) for what a key has to
guarantee. The conformance suite, which now ships with the package, decides if
you got it right.

These two changes cannot be deprecated. A method signature is not a name, and
an alias pointing at a class whose methods take different arguments would break
at the first call rather than warn.

### The agent an operation acts as is named by keyword

`Control.write`, `Control.set_premise` and `Control.ack` take the agent's name
as a keyword, after the arguments that say what to do.

```python
# 0.7
control.write("triage", "findings", body)
control.set_premise("triage", "window", value, 3)
control.ack("triage", notification_id)
```

```python
# 0.8
control.write("findings", body, writer="triage")
control.set_premise("window", value, 3, writer="triage")
control.ack(notification_id, agent="triage")
```

The old order was three strings, and `BoardClient.write` takes the same three
in a different order. Pasting the client's line against a `Control` passed
`mypy --strict`, and filed the contribution under an agent named after the
level. A keyword cannot be pasted into the wrong position.

`Control.ack` also accepts a plain `int`, so a notification identifier read off
the wire needs no cast.

`Control.as_agent(name)` is new, and returns the board as that agent sees it.
It satisfies `AgentBoard`, which `BoardClient` satisfies too, so an agent body
written against that protocol runs in process and over HTTP unchanged.

### A wrong region name raises on every path

Writing to a region nobody declared raises `UndeclaredRegionError` instead of
returning `Rejected`; raising is what reading an undeclared region has always
done.

```python
# 0.7
outcome = control.write("rumours", body, writer="triage")
if isinstance(outcome, Rejected) and outcome.cause is RejectionCause.UNDECLARED_REGION:
    ...
```

```python
# 0.8
try:
    outcome = control.write("rumours", body, writer="triage")
except UndeclaredRegionError:
    ...
```

`RejectionCause.UNDECLARED_REGION` is removed. Over HTTP the same request
answers 404 with `error: unknown_region` rather than 422 with `cause:
undeclared_region`, and `BoardClient` raises `UndeclaredRegionError` for it, so
an agent using the client needs no change.

Permitting an agent to write to a region that was declared as a premise now
raises `RegionKindError` saying it is a premise, rather than
`UndeclaredRegionError` naming a region that is declared.

ADR 0016 in the repository records the axis: the library raises for what the
application's own configuration settles, and returns for what the run's policy
decides.

### Two constructors refuse what they used to accept

`RunLimits` is keyword only, and `SqliteStore` requires its path.

| Was | Is |
| --- | --- |
| `RunLimits(wall, idle)` | `RunLimits(wall_clock=wall, idle=idle)` |
| `SqliteStore()` | `SqliteStore(":memory:")` |

Both fields of `RunLimits` are a `timedelta`, so a swapped pair was accepted
and the run ended at the wrong time with the wrong outcome. Every call site in
this repository already used keywords, so nothing else moved.

`SqliteStore` defaulted to holding the record in the process. A second store
constructed over that default in one process shares nothing with the first, so
it reads an empty board and refuses every write against a region the first
declared. Where the record is kept is stated rather than defaulted, which is
the rule `create_model` already follows for `store`.

### A reused idempotency key raises

A key that names a region it did not name before raises `IdempotencyKeyError`,
which is what the store already did. `RejectionCause.IDEMPOTENCY_KEY_REUSED` is
removed, and over HTTP the answer is 409 with `error: idempotency_key_reused`
rather than 422.

409 now carries two answers. A `ConflictBody` means the premise moved on and
the caller reads it and decides again. An `ErrorBody` naming
`idempotency_key_reused` means the key is wrong, and `BoardClient` raises
`IdempotencyKeyError` rather than returning a `Conflict`.

### Databases an earlier version wrote

Your database needs no migration by hand. The store rename moved no rows,
because they were already scoped by `board_id`, and the three stores that keep
a record on disk add what a key needs to a database that 0.7 wrote.
`SqliteStore` adds its two columns when it opens the file. `PostgresStore` and
`MongoStore` add theirs when you call `create_schema` or `create_indexes`,
which an application already calls once at startup.

Those three now stamp a record with a schema number and check that number when
they open. A database written by 0.7 or earlier carries no stamp and is adopted
rather than refused, because 0.8 reads everything those versions wrote. From
here on, a database written for a schema the library cannot read is refused
when the store opens rather than at whichever query touches the change.

## 0.6 to 0.7

Every name 0.5 deprecated is gone. 0.6 said it removed them and did not, so 0.7
removes them.

If you are moving from 0.4 or 0.5 and still use any old name, the table under
[0.4 to 0.5](#04-to-05) gives its replacement. Nothing else about them changed:
each replacement has behaved identically since 0.5.

| Removed | Use |
| --- | --- |
| `Register`, `RegisterState`, `UnsetRegisterError`, `ProposedRegisterWrite` | `Premise`, `PremiseState`, `UnsetPremiseError`, `ProposedPremiseWrite` |
| `RegisterSeeded`, `SeedError` | `PremiseOpened`, `PremiseError` |
| `RunBudgets`, and the `budgets` keyword | `RunLimits`, and the `limits` keyword |
| `Accepted` | `Written` |
| `read_register`, `set_register` | `read_premise`, `set_premise` |
| the `seed` keyword | the `premises` keyword |
| `ProposedContribution.agent` | `ProposedContribution.writer` |

`premises` and `limits` are now required arguments to `create_model` rather
than optional ones, because the keywords they replaced are gone and one of each
pair had to be given anyway.

## 0.5 to 0.6

A creator names the agents a run starts with. Nothing else changes, and nothing
that worked before stops working, because the argument is optional.

```python
# 0.5
model = create_model(regions=[...], premises={...}, limits=..., board=...)
model.control.register_agent(Agent(name="ocp", notify=investigate))
```

```python
# 0.6
model = create_model(
    regions=[...],
    premises={...},
    agents=[Agent(name="ocp", notify=investigate)],
    limits=...,
    board=...,
)
```

`register_agent` is unchanged and is now the path for an agent joining a run
already under way.

The reason to move is the wall clock. It is armed while the run is constructed,
so a run that waits to be discovered spends its own time on discovery. Naming
the agents at creation leaves nothing to discover. A run created with no agents
named still starts its clock, so registering afterwards spends it. [The
run](concepts/run.md#how-agents-join) covers what that costs.

## 0.4 to 0.5

The region that holds what the work is given was a `Register`. It is a
`Premise`. `register` belongs to computer architecture, and the specification
that coined it cited nothing for it while citing Nii for *solution space*,
*partial solution* and *skeletal*. The word now appears in the package in one
place, `register_agent`, where it is a verb.

Renaming the region for what it holds also removed `seed`: a premise has a
value, and the value it starts with is given by `premises`.

### Names

| Was | Is | Removed in |
| --- | --- | --- |
| `Register` | `Premise` | 0.7.0 |
| `RegisterState` | `PremiseState` | 0.7.0 |
| `UnsetRegisterError` | `UnsetPremiseError` | 0.7.0 |
| `ProposedRegisterWrite` | `ProposedPremiseWrite` | 0.7.0 |
| `RegisterSeeded` | `PremiseOpened` | 0.7.0 |
| `SeedError` | `PremiseError` | 0.7.0 |
| `RunBudgets` | `RunLimits` | 0.7.0 |
| `Accepted` | `Written` | 0.7.0 |
| `read_register` | `read_premise` | 0.7.0 |
| `set_register` | `set_premise` | 0.7.0 |
| `create_model(seed=...)` | `create_model(premises=...)` | 0.7.0 |
| `Control(budgets=...)` | `Control(limits=...)` | 0.7.0 |
| `Control.write(agent=...)` | `Control.write(writer=...)` | already gone |
| `ProposedContribution.agent` | `ProposedContribution.writer` | 0.7.0 |

Each of these worked, with a warning, in 0.5 and 0.6. All of them were removed
in 0.7.

### What has no alias

`BoardStore` declares `read_premise`. A protocol method cannot be aliased, so
an adapter of your own must rename its `read_register` method. The conformance
suite fails immediately if it has not been renamed.

### A write that landed

`Control.write` returned `Accepted` and `Control.set_premise` returned
`Written`. Both return `Written` now. A level holds no version, so `version` is
`None` there.

```python
result = model.control.write("platform", {"findings": ["oom"]}, writer="ocp")
if isinstance(result, Written):
    print(result.sequence)
```

### Before and after

```python
# 0.4
model = create_model(
    regions=[Level("platform"), Register("window")],
    seed={"window": ["20:00", "22:00"]},
    budgets=RunBudgets(wall_clock=timedelta(minutes=10), idle=timedelta(seconds=1)),
    board=SqliteStore("incident.sqlite3"),
)
window = model.reader.read_register("window").value
model.control.set_register("ocp", "window", ["21:00"], expected_version=1)
```

```python
# 0.5
model = create_model(
    regions=[Level("platform"), Premise("window")],
    premises={"window": ["20:00", "22:00"]},
    limits=RunLimits(wall_clock=timedelta(minutes=10), idle=timedelta(seconds=1)),
    board=SqliteStore("incident.sqlite3"),
)
window = model.reader.read_premise("window").value
model.control.set_premise("window", ["21:00"], expected_version=1, writer="ocp")
```

### Your database

The storage identifiers changed with the names, so a database that a 0.4 run
wrote needs one migration before a 0.5 run opens against it. A fresh database
needs nothing. [Storage](concepts/storage.md#moving-a-database-written-by-040)
carries the statements for Postgres, MongoDB and SQLite, each of which was run
against a real server and read the old record back.

## 0.3 to 0.4

| Was | Is | Note |
| --- | --- | --- |
| `Board` | `InMemoryStore` | It is a test double, and it is named as one |
| `create_model` with no board | `create_model(board=...)` | Required, because a run whose record no second process can read is not a shared solution model |

Content crosses every board as JSON from 0.4 onwards. A tuple written reads
back as a list, and content that JSON cannot carry raises `TypeError` at the
write rather than at the first process boundary.
