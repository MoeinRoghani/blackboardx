# Migrating

Every name that moved, what replaced it, and the release its old form stops working in.

## 0.5 to 0.6

A creator names the agents a run starts with. Nothing else changes, and nothing that worked before stops working, because the argument is optional.

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

`register_agent` is unchanged and is now the path for an agent joining a run already under way.

The reason to move is the wall clock. It is armed while the run is constructed, so a run that waits to be discovered spends its own time on discovery. Naming the agents at creation leaves nothing to discover. A run created with no agents named still starts its clock, so registering afterwards spends it. [The run](concepts/run.md#how-agents-join) covers what that costs.

## 0.4 to 0.5

The region that holds what the work is given was a `Register`. It is a `Premise`. `register` belongs to computer architecture, and the specification that coined it cited nothing for it while citing Nii for *solution space*, *partial solution* and *skeletal*. The word now appears in the package in one place, `register_agent`, where it is a verb.

Renaming the region for what it holds also removed `seed`: a premise has a value, and the value it starts with is given by `premises`.

### Names

| Was | Is | Old form works until |
| --- | --- | --- |
| `Register` | `Premise` | 0.6.0 |
| `RegisterState` | `PremiseState` | 0.6.0 |
| `UnsetRegisterError` | `UnsetPremiseError` | 0.6.0 |
| `ProposedRegisterWrite` | `ProposedPremiseWrite` | 0.6.0 |
| `RegisterSeeded` | `PremiseOpened` | 0.6.0 |
| `SeedError` | `PremiseError` | 0.6.0 |
| `RunBudgets` | `RunLimits` | 0.6.0 |
| `Accepted` | `Written` | 0.6.0 |
| `read_register` | `read_premise` | 0.6.0 |
| `set_register` | `set_premise` | 0.6.0 |
| `create_model(seed=...)` | `create_model(premises=...)` | 0.6.0 |
| `Control(budgets=...)` | `Control(limits=...)` | 0.6.0 |
| `Control.write(agent=...)` | `Control.write(writer=...)` | already gone |
| `ProposedContribution.agent` | `ProposedContribution.writer` | 0.6.0 |

Each old name still imports and resolves to its replacement, so `isinstance` and equality keep working while you move. Using one warns and names both the replacement and the release that removes it.

### What has no alias

`BoardStore` declares `read_premise`. A protocol method cannot be aliased, so an adapter of your own must rename its `read_register` method. The conformance suite fails immediately if it has not been renamed.

### A write that landed

`Control.write` returned `Accepted` and `Control.set_premise` returned `Written`. Both return `Written` now. A level holds no version, so `version` is `None` there.

```python
result = model.control.write("ocp", "platform", {"findings": ["oom"]})
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
    board=SqliteBoard("incident.sqlite3"),
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
    board=SqliteBoard("incident.sqlite3"),
)
window = model.reader.read_premise("window").value
model.control.set_premise("ocp", "window", ["21:00"], expected_version=1)
```

### Your database

The storage identifiers changed with the names, so a database a 0.4 run wrote needs one migration before a 0.5 run opens against it. A fresh database needs nothing. [Storage](concepts/storage.md#moving-a-database-written-by-040) carries the statements for Postgres, MongoDB and SQLite, each of which was run against a real server and read the old record back.

## 0.3 to 0.4

| Was | Is | Note |
| --- | --- | --- |
| `Board` | `InMemoryBoard` | It is a test double, and it is named as one |
| `create_model` with no board | `create_model(board=...)` | Required, because a run whose record no second process can read is not a shared solution model |

Content crosses every board as JSON from 0.4 onwards. A tuple written reads back as a list, and content JSON cannot carry raises `TypeError` at the write rather than at the first process boundary.
