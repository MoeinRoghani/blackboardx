# ADR 0013: A store holds many boards, and every call names one

Date: 2026-09-02

## Status

Accepted. Supersedes ADR 0011 on where a board's identity lives. The rule that ADR 0011 established, that storage is chosen and never defaulted, is kept.

## Context

A board object was built for one blackboard and carried that blackboard's identity:

```python
PostgresBoard(pool, board_id="incident-4471")
```

A connection to Postgres serves every blackboard an application runs, not one. Binding it to a single run made an infrastructure object pretend to be a domain object, and an application paid for that twice: it built a new object for every blackboard it created, and again for every read that did not need the control component.

The default made it worse. `board_id` defaulted to `"default"`, so two boards over one file silently shared rows, and a board that never wrote anything read what another board had written.

Every comparable library keeps the two apart. LangGraph's checkpointer takes the run identity as the first parameter of every method, and its classes are named for the store rather than for a run. Temporal holds one client and passes the workflow identity per call. Neither builds a storage object per run.

## Decision

A store holds many boards. Every operation names the board it acts on, as its first argument.

```python
store = PostgresStore(pool)
store.read_premise("incident-4471", "window")
```

The four implementations are named for what they are. `InMemoryStore`, `SqliteStore`, `PostgresStore` and `MongoStore` each hold many boards.

`create_model` takes the board identity and the store separately. The caller supplies the identity, because only the caller knows what identifies a run in its own system, and the library never reads it:

```python
create_model(board_id=..., store=..., regions=..., premises=..., limits=...)
```

`BoardStore` and `BoardReader` diverge. The store names a board on every call. The reader is bound to one board, so a rule, a termination predicate, and `model.reader` read without repeating an identifier they cannot vary.

The library reads no connection string from the environment. The caller passes the connection, as LangGraph does.

## Consequences

- One store serves every board an application runs, so a service builds one at startup rather than one per run.
- A read that does not need the control component costs one call on the store the service already holds.
- The `"default"` identifier is gone, and with it the case where two boards silently shared rows.
- A notification carries the board it came from, which an agent serving several boards needs.
- The change cannot be deprecated. A method signature is not a name, and an alias pointing at a class whose methods take different arguments would break at the first call rather than warn. The migration page carries the mapping instead.

## Alternatives rejected

- **The identity on `create_model` alone, with the store still bound.** The caller would pass it twice, once to the store and once to the model, and nothing would catch a mismatched pair.
- **The library generating the identity.** A caller could not correlate a board to its own records, and a retried creation would produce two boards rather than one detectable duplicate.
- **Reading a connection string from the environment.** No comparable library does it. It hides which store a run is using from the code that creates the run.
