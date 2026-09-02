# Running as a service

The library is in-process. An application whose agents are separately deployed services puts one service in front of it, and that service is the only thing that holds a `Control` and the only thing that reaches the database.

Read [what is durable and what is not](#what-is-durable-and-what-is-not) before deciding how many replicas that service runs, and [what this version does not do](../limits.md) before deciding to build on it.

## The parts

| Part | What it is | Ours |
| --- | --- | --- |
| `blackboardx` | This package | yes |
| Storage adapter | A `BoardStore` implementation against your database | `PostgresStore` or `MongoStore`, or you write one |
| Blackboard service | A container importing the library, serving HTTP | the routing and the answers, not the server |
| Run registry | Which boards this replica holds a run for | `on_open` and `on_closed` hand you each entry; the dictionary is yours |
| Agent client | What agents import to call it | `BoardClient` and `AsyncBoardClient` |
| Database | One primary you already run | no |
| Retention | Deciding when a finished run's record goes | `store.delete`, when you call it |
| Agents | Independent deployments | no |

The package ships `PostgresStore` and `MongoStore` for a deployment and `SqliteStore` for one machine, all satisfying the `BoardStore` protocol. Against any other database the eight methods are yours to write: four read, three write, and one removes a board. Every rule they are held to maps onto ordinary primitives. [Storage](storage.md) covers what each has to guarantee.

## What is durable and what is not

A store makes the **record** durable. It does not make the **run** durable, and the difference decides how the service is deployed.

| Held where | What |
| --- | --- |
| The database, through the store | Regions, contributions, premise values and versions, the sequence, idempotency keys |
| The process, in `Control` | The registered agents, their cursors, outstanding notifications, the notification identifiers, the audit, the idle and wall clock timers |
| The process, in `HttpNotifier` | Notifications queued but not yet sent |

A `Control` lives in one process. A second replica holding its own `Control` for the same board knows no agent the first registered, owes no notification the first dispatched, and measures silence from its own start. Losing the replica that holds one ends that run: the record survives and the run does not resume.

So one board is written through one `Control` in one process at a time. Writes are scaled by putting different boards on different replicas and routing by board identifier.

Reads are not bound that way. `BoardService` takes the store as well as the registry, and answers the four `GET` operations from the record whenever it holds no run for the board, so any replica holding the store answers a read for any board in it. A board the store never held answers 404 either way, so a mistyped identifier is not answered with an empty board. The audit is the one read that stays with the run, because it lives in the process and no operation on the wire exposes it.

Making the run itself durable, so that a replacement resumes rather than restarts, means putting the registry, the outstanding notifications, and the deadlines in the database alongside the record. That is not in the library today, and `docs/design/durable-runs.md` in the repository sets out what it would take.

A notification lost with the process usually costs nothing, since a notification carries no values and the next one covers the range a lost one would have covered. It costs something when the lost one is the last, and the run then waits until its idle limit closes it.

## Holding the runs

`BoardService` asks a callable of yours for the `Control` a request names, so the service keeps a dictionary of the boards this replica holds a run for. `on_open` and `on_closed` fill and empty that dictionary.

```python
runs: dict[str, Control] = {}


def opened(model: Model) -> None:
    runs[model.board_id] = model.control


model = create_model(
    board_id="incident-3391",
    # the store, the regions, the opening premises and the limits as before
    on_open=opened,
    on_closed=lambda outcome: runs.pop("incident-3391", None),
)

service = BoardService(runs.get, store=store, prefix="/v1")
```

`on_open` receives the model and reads `board_id` off it. `on_closed` receives the outcome, which names no board, so the identifier comes from the call that opened the run.

`on_open` runs once the premises hold their values and before the first agent is registered. Registering an agent runs that agent's callback on this thread, so without `on_open` an agent that reads back through the service meets 404 for the board that is creating it.

Both callbacks are application code at the library's boundary, on the terms `Agent.notify` already has: neither may block, and an exception either raises is suppressed, because a registry that is down must not abort a run that has opened.

## Replacing a replica

A replica that dies takes its run with it and leaves the record. The replacement opens a run over that record with `attach_model`, which declares nothing and takes no opening premises.

```python
model = attach_model(
    board_id="incident-3391",
    store=store,
    regions=[Level("signals"), Premise("severity")],
    agents=[Agent(name="triage", notify=deliver, subscribes_to={"signals"})],
    limits=RunLimits(wall_clock=timedelta(minutes=30), idle=timedelta(seconds=30)),
    on_open=opened,
)
```

`regions` still names what the run expects and is checked against the record, so a replica pointed at the wrong identifier is told rather than left to fail at its first write. A board the store holds no regions for is refused, because attaching to nothing would build a run whose every write is rejected.

| Carries over | Starts again |
| --- | --- |
| The regions and their kinds | The registered agents |
| Every contribution | Their cursors |
| Premise values and their versions | The outstanding notifications |
| The sequence counter | The audit |
| The idempotency keys | The notification identifiers, from one |

An agent registered against the attached run is woken as one joining a run already under way, covering everything on the board, which is what an agent that lost its own memory of the run needs. Work it had finished but not acknowledged is done again unless its writes carried idempotency keys. Those keys are on the record, so a repeat answers with the first write's sequence and adds nothing.

## The path a call takes

Agents reach the board only through the service. They open no connection to the database and hold no `Control`, and the client in `blackboard.agent` is what they call it with.

```
agent  ──HTTP──▶  blackboard service  ──▶  blackboardx  ──▶  database
   ▲                      │
   └──────notification────┘
```

## What the service writes, and what it does not

The service owns its HTTP server, the prefix it mounts under, and its authentication, because the framework and the gateway are its own. Everything under that prefix the library supplies.

| | Whose |
| --- | --- |
| The HTTP server, the mount prefix, the authentication | The service |
| The paths and the methods | `blackboard.wire` |
| Matching a request, and the status each answer carries | `blackboard.server` |
| Building the request and reading the answer | `blackboard.agent` |
| Admitting the write, ordering it, storing it | `blackboard` |
| Sending the notification, retrying it, reporting a failure | `blackboard.delivery` |

Serialisation is not on the service's side of that line, because content already crosses every board as JSON, so a contribution has the same form on the wire that it has in the record.

**Delivery.** `HttpNotifier.to(url)` returns a lane, which is the callable an `Agent` is created with. It queues the notification and returns, so the agent that wrote is not made to wait, and it sends on a worker of its own, so one agent that is slow delays nobody else. Each call to `to` opens a lane, and closing one releases its worker without closing the notifier or any other lane. [Notify agents over HTTP](../guides/notifying-agents.md) covers the retry policy and what a failure looks like.

**Idempotency.** An HTTP retry must not append a contribution twice, so a write carries an idempotency key the caller chose and the store writes that key once. The service passes the key through and deduplicates nothing itself. The client attempts a write again only where it carries a key; without one an unreachable blackboard raises rather than risking a second contribution.

## What does not change

Regions, admission, subscription, notification, and the three outcomes behave identically whether the caller is in the same process or across a network, because the control component only ever learns that an agent stopped.

`AgentBoard` is the four reads and the three writes without the agent's own name. `Control.as_agent(name)` returns one in process and `BoardClient` satisfies one over HTTP, so an agent body written against it moves between the two deployments unchanged.
