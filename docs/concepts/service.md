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

| | What, and where it lives |
| --- | --- |
| **Run state** | Regions, contributions, premise values and versions, the sequence, idempotency keys, the run's two deadlines and its outcome, and how far each agent has been notified and has answered. All of it is in the store, whichever store that is. |
| **Configuration** | The regions, the agent roster, the admission rule, the termination predicate, the limits and the clock. Every replica is given these, the way every replica is given the same image and the same environment. |
| **In flight** | Notifications `HttpNotifier` has queued but not yet sent. |

A `Model` is a handle to a board that lives in the store, not the board
itself. It holds nothing, so holding one keeps no run open, caches no
registry, and reserves nothing; build one where convenient and discard it.
The name invites the other reading, which is why it is said here.

Replicas are identical, so each is given the same configuration and each reads the same run state. A second replica therefore measures silence from the same instant, closes the run on the same deadline, knows which agents are owed an answer, and holds the same roster with the same addresses. A write is served by whichever replica receives it, and that replica notifies on the write path.

`Control.notify_due` covers what the write path cannot. It reads what has landed since each agent last answered and delivers it, so a change taken while a replica was starting, or one whose delivery was lost, still reaches the agent. A run inside one process finds nothing to do there. Schedule it beside `close_expired`.

An application that calls `register_agent` at run time on one replica has told one replica something the others were not told. That is the same mistake as running replicas with different configuration, and the library does not repair it: put the agent in the roster every replica loads.

Reads are not bound that way. `BoardService` takes the store as well as the registry, and answers the four `GET` operations from the record whenever the replica holds no run for the board, so any replica holding the store answers a read for any board in that store. A board that the store never held answers 404 in both cases, so a mistyped identifier is not answered with an empty board. The audit is the one read that stays with the run, because it lives in the process and no operation on the wire exposes it.

A replacement replica resumes rather than restarts. The deadlines, the outcome and how far each agent answered are on the record, so a replica that takes over closes the run on the original deadline and does not tell an agent again what it has already answered.

A notification lost with the process usually costs nothing, since a notification carries no values and the next one covers the range the lost one would have covered. It costs something when it is the last one, because nothing then follows it, and the run waits until its idle limit closes it.

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
