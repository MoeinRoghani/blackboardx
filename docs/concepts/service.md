# Running as a service

An application whose agents are separately deployed services puts one service in front of the library, and that service is the only thing that reaches the database.

Every replica of that service is identical: same image, same configuration, same store. **Any replica serves any board.** Nothing is pinned to a replica, so requests may be routed round robin, by sticky session, through a mesh, or across clusters, and the library has no opinion about which.

Read [what a store holds](storage.md#what-a-store-holds) for why that is true, and [what this version does not do](../limits.md) before building on it.

What each part is made of is covered by [the board](board.md),
[the control component](control.md) and [the run](run.md); this page is about
deploying them.

## The parts

| Part | What it is | Ours |
| --- | --- | --- |
| `blackboardx` | This package | yes |
| Storage adapter | A `BoardStore` implementation against your database | `PostgresStore` or `MongoStore`, or you write one |
| Blackboard service | A container importing the library, serving HTTP | the routing and the answers, not the server |
| Scheduled work | Closing runs nobody is watching, and sending what was never delivered | `close_expired`, `Control.relay` and `Sweep`; the schedule is yours |
| Agent client | What agents import to call it | `BoardClient` and `AsyncBoardClient` |
| Database | One primary you already run | no |
| Retention | Deciding when a finished run's record goes | `store.delete`, when you call it |
| Agents | Independent deployments | no |

The package ships `PostgresStore` and `MongoStore` for a deployment and `SqliteStore` for one machine, all satisfying the `BoardStore` protocol. Against any other database the eighteen methods are yours to write: four read the record, three write to it, one removes a board, five hold the run, three hold how far each agent has been notified and has answered, and two hold what a write recorded and nothing has sent. Every rule they are held to maps onto ordinary primitives. [Storage](storage.md) covers what each has to guarantee.

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

A replacement replica resumes rather than restarts. The deadlines, the outcome and how far each agent answered are on the record, so any replica closes the run on the original deadline and tells no agent again what it has already answered.

A notification a process was holding when it stopped is not lost. The intent was recorded with the write, so `Control.relay` on any replica holding that agent sends it. Delivery is at least once, and a repeat costs nothing because a notification carries no values.

## Serving a board

`BoardService` asks a callable of yours for the `Control` a request names. A `Control` is a handle: it binds a board identifier and the configuration and reads the store on every call, so building one costs a little object and no round trip, and dropping one closes nothing.

That leaves two shapes, and both are correct:

| Shape | When |
| --- | --- |
| Build a `Control` per request from the board identifier | The simplest. Nothing to keep, nothing to evict, no replica differs from another. |
| Keep a dictionary of them | Saves rebuilding the handle. `on_open` and `on_closed` fill and empty it. |

A dictionary is a cache of handles and not a claim on a board. A replica whose dictionary lacks an entry builds one; a replica that has one is not thereby the owner of anything.

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

`on_open` receives the model and reads `board_id` off it. `on_closed` receives the outcome, which names no board, so the identifier comes from the call that created the model.

`on_open` runs once the premises hold their values and before the first agent is registered. Registering an agent runs that agent's callback on this thread, so without `on_open` an agent that reads back through the service meets 404 for the board whose creation registered it.

Both callbacks are application code at the library's boundary, on the terms `Agent.notify` already has: no callback may block, and an exception that a callback raises is suppressed, because a registry that is down must not abort a run that has opened.

## Losing a replica

A replica that stops takes nothing with it. Its deadlines, its outcome, how
far each agent answered and what it had not yet delivered are all on the
record, so another replica reads them and carries on.

| What the replacement does | How |
| --- | --- |
| Serves reads and writes for that board | `create_model` with the same arguments. It converges on the board rather than refusing it. |
| Closes the run on the original deadline | The deadline is an instant in the store, not a timer in a process |
| Avoids telling an agent what it already answered | The cursor is on the record |
| Sends what the lost replica never delivered | `Control.relay` |

There is nothing to take over, because nothing was held. `attach_model` was
the door a replacement used when a run lived in one process; it is deprecated
and `create_model` serves both cases.

Two identifiers for one board is still the application's mistake to avoid.

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
