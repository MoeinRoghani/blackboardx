# Running as a service

The library is in-process. An application whose agents are separately deployed services puts one service in front of the library, and that service is the only thing that holds a `Control` and the only thing that reaches the database.

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

The package ships `PostgresStore` and `MongoStore` for a deployment and `SqliteStore` for one machine, all satisfying the `BoardStore` protocol. Against any other database the sixteen methods are yours to write: four read the record, three write to it, one removes a board, five hold the run, and three hold how far each agent has been notified and has answered. Every rule they are held to maps onto ordinary primitives. [Storage](storage.md) covers what each has to guarantee.

## What is durable and what is not

A store makes the **record** durable. It does not make the **run** durable, and the difference decides how the service is deployed.

| Held where | What |
| --- | --- |
| The database, through the store | Regions, contributions, premise values and versions, the sequence, idempotency keys, the run's two deadlines and its outcome, and how far each agent has been notified and has answered |
| The process, in `Control` | The registered agents, being their callbacks and subscriptions, and the audit |
| The process, in `HttpNotifier` | Notifications queued but not yet sent |

A second replica reads all of the left column, so it measures silence from the same instant, closes the run on the same deadline, and knows which agents are owed an answer. What it does not have is a callback for an agent that registered elsewhere, because a callback is not a thing a database holds.

So a write is served by any replica, and the replica an agent registered with is the one that wakes it. That process calls `notify_due` on whatever schedule suits the deployment, which reads what has landed since each of its agents last answered and delivers it. A write taken in the same process notifies inline and needs no poll.

Reads are not bound that way. `BoardService` takes the store as well as the registry, and answers the four `GET` operations from the record whenever the replica holds no run for the board, so any replica holding the store answers a read for any board in that store. A board that the store never held answers 404 in both cases, so a mistyped identifier is not answered with an empty board. The audit is the one read that stays with the run, because it lives in the process and no operation on the wire exposes it.

Making the run itself durable, so that a replacement resumes rather than restarts, means putting the registry, the outstanding notifications, and the deadlines in the database alongside the record. That is not in the library today, and `docs/design/durable-runs.md` in the repository sets out what it would take.

A notification lost with the process usually costs nothing, since a notification carries no values and the next notification covers the range the lost one would have covered. A lost notification costs something when it is the last one, and the run then waits until its idle limit closes it.

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

`on_open` runs once the premises hold their values and before the first agent is registered. Registering an agent runs that agent's callback on this thread, so without `on_open` an agent that reads back through the service meets 404 for the board whose creation registered it.

Both callbacks are application code at the library's boundary, on the terms `Agent.notify` already has: no callback may block, and an exception that a callback raises is suppressed, because a registry that is down must not abort a run that has opened.

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

`regions` still names what the run expects and is checked against the record, so a replica pointed at the wrong identifier is refused rather than left to fail at its first write. A board that the store holds no regions for is refused, because attaching to nothing would build a run whose every write is rejected.

| Carries over | Starts again |
| --- | --- |
| The regions and their kinds | The registered agents, being their callbacks and their subscriptions |
| Every contribution | The audit |
| Premise values and their versions | |
| The sequence counter | |
| The idempotency keys | |
| How far each agent has been notified and has answered | |

An agent registered against the attached run resumes from what it answered, because that number is on the record rather than in the process that told it. Work it had finished and acknowledged is not done again. Work it had finished without acknowledging is, unless its writes carried idempotency keys; those keys are on the record, so a repeat answers with the first write's sequence and adds nothing.

## The path a call takes

Agents reach the board only through the service. They open no connection to the database and hold no `Control`, and the client in `blackboard.agent` is what they call the service with.

```
agent  ──HTTP──▶  blackboard service  ──▶  blackboardx  ──▶  database
   ▲                      │
   └──────notification────┘
```

## What the service writes, and what it does not

The service owns its HTTP server, the prefix it mounts under, and its authentication, because the framework and the gateway are its own. The library supplies everything under that prefix.

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

**Idempotency.** An HTTP retry must not append a contribution twice, so a write carries an idempotency key the caller chose and the store writes that key once. The service passes the key through and deduplicates nothing itself. The client attempts a write again only where it carries a key; without a key, an unreachable blackboard raises rather than risking a second contribution.

## What does not change

Regions, admission, subscription, notification, and the three outcomes behave identically in the same process and across a network, because the control component only ever learns that an agent stopped.

`AgentBoard` is the four reads and the three writes without the agent's own name. `Control.as_agent(name)` returns an `AgentBoard` in process and `BoardClient` satisfies `AgentBoard` over HTTP, so an agent body written against it moves between the two deployments unchanged.
