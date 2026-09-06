# Running as a service

An application whose agents are separately deployed services puts one service in front of the library, and that service is the only thing that reaches the database.

Every replica of that service is identical: same image, same callables, same store. **Any replica serves any board.** Nothing is pinned to a replica, so requests may be routed round robin, by sticky session, through a mesh, or across clusters, and the library has no opinion about which.

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
| Scheduled work | Closing runs nobody is watching, and sending what was never delivered | `close_expired` and `relay_unsent`, or `Sweep` running both; the schedule is yours |
| Agent client | What agents import to call it | `BoardClient` and `AsyncBoardClient` |
| Database | One primary you already run | no |
| Retention | Deciding when a finished run's record goes | `store.delete`, when you call it |
| Agents | Independent deployments | no |

The package ships `PostgresStore` and `MongoStore` for a deployment and `SqliteStore` for one machine, all satisfying the `BoardStore` protocol. Against any other database the nineteen methods are yours to write: four read the record, three write to it, one removes a board, five hold the run, four hold its agents and how far each has got, and two hold what a write recorded and nothing has sent. Every rule they are held to maps onto ordinary primitives. [Storage](storage.md) covers what each has to guarantee.

## What is written down and what is not

A store makes the record durable, and the run with it. What no store holds is a function, and that is the difference the deployment turns on.

| Word | Holds |
| --- | --- |
| **The record** | Regions, contributions, premise values and their versions, the sequence, idempotency keys, and the run's outcome with the agents that did not finish. Removed only by `store.delete`. |
| **The run** | The two deadlines, its agents with what wakes each and where it is reached, how far each has got, and what a write recorded that nothing has sent. Removed when the run closes. |
| **The callables** | `admission_rule`, `termination_predicate`, `clock`, `on_open`, `on_closed`, and the transport that reaches an address. Never written, so never removed. |

The first two are in the store, whichever store that is. The third is supplied
to [`create_model`](run.md) on every construction, because a function is not
data. [Storage](storage.md#what-a-store-holds) covers the split.

Replicas are identical, so each is given the same callables, and each reads the same record and the same run. A second replica therefore measures silence from the same instant, closes the run on the same deadline, knows which agents the run holds and where each is reached, and knows which are owed an answer. A write is served by whichever replica receives it, and that replica records who should hear it by reading the run rather than by consulting a roster of its own.

`Control.notify_due` covers what the write path cannot. It reads what has landed since each agent last answered and delivers it, so a change taken while a replica was starting, or one whose delivery was lost, still reaches the agent. A run inside one process finds nothing to do there. Schedule it beside `close_expired`.

A board starts with its agents, named to `create_model`. `register_agent` covers the one case that cannot: an agent joining a run already under way. Both write the same row to the run, so the two differ in when and not in what, and after either the agent is on the record rather than in a process.

Where the application gets its agents is its own affair: hardcoded, pulled from a vault, read from its own table, or carried in the request that triggered the run. This library takes no view and owns no registry.

Which agents a write should wake is read from the run, so a replica that never saw an agent declared still records that it is owed a notification. Reaching it needs one of two things: a callable this process holds, or the address the run records and a transport given as `reach`. `HttpNotifier.reach` is one.

Reads are not bound that way. `BoardService` takes the store as well as the registry, and answers the four `GET` operations from the record whenever the replica holds no run for the board, so any replica holding the store answers a read for any board in that store. A board that the store never held answers 404 in both cases, so a mistyped identifier is not answered with an empty board.

A replacement replica resumes rather than restarts. The deadlines, the outcome and how far each agent answered are on the record, so any replica closes the run on the original deadline and tells no agent again what it has already answered.

A notification a process was holding when it stopped is not lost. The intent was recorded with the write, so `Control.relay` sends it from any replica that can reach the agent, which is one holding its callable or one holding a transport for the address the run records. Delivery is at least once, and a repeat costs nothing because a notification carries no values.

## Why this is safe with several replicas

Nothing takes a lock and nothing elects a leader. Every job below is either
atomic in the store, or benign when two replicas do it at once.

Read the third column first: a job that happens during a request runs in the
replica serving it, and a job that happens when no request is in flight cannot.

| Job | When | Runs where | Safe because |
| --- | --- | --- | --- |
| Apply the admission rule | During a write | The serving replica | It judges the board as read, and concurrent judgement is the documented behaviour |
| Sequence and store the write | During a write | The store | The store assigns the sequence |
| Check the premise version | During a write | The store | A compare and set |
| Enforce the idempotency key | During a write | The store | A unique index |
| Record who should hear of it | During a write | The store | The same transaction as the contribution, so the intent cannot be lost apart from the write |
| Give a notification its identifier | On dispatch | Nowhere | It is the sequence the range ends at. Nothing allocates it. |
| Raise how far an agent was told | Dispatch, and acknowledgment | The store | Both numbers only rise, so either order is correct |
| Push the idle deadline out | On every event | The store | Both replicas push it forward; either order is correct |
| Decide a notification is due | A write, or a window closing | The serving replica | A repeat is harmless: a notification carries no values |
| Send it | After the write commits | Any replica, or the relay | At least once, and repeats are free by the line above |
| Close a run somebody asked about | On any access | The serving replica | A compare and set on the outcome: the first writer wins |
| Close a run nobody asked about | Periodically | Wherever you call it | The same compare and set |
| Relay what nothing has sent | Periodically | Wherever you call it | A row is marked only after a send, so two replicas relaying together send twice and repeats are free |
| Ask the termination predicate | At the deadline | Whoever is closing | Its answer is discarded if the board moved |

A replica does not own a board, so there is nothing to hand over when one
stops and nothing to route around.

## Serving a board

`BoardService` asks a callable of yours for the `Control` a request names. A `Control` is a handle: it binds a board identifier and the callables and reads the record and the run on every call, so building one costs a little object and no round trip, and dropping one closes nothing.

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
