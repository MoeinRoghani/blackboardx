# Running as a service

The library is in-process. An application whose agents are separately deployed services puts one service in front of it, and that service is the only thing that imports the library and the only thing that reaches the database.

Read [what is durable and what is not](#what-is-durable-and-what-is-not) before deciding how many replicas that service runs, and [what this version does not do](../limits.md) before deciding to build on it.

## The parts

| Part | What it is | Ours |
| --- | --- | --- |
| `blackboardx` | This package | yes |
| Storage adapter | A `BoardStore` implementation against your database | `PostgresStore` or `MongoStore`, or you write one |
| Blackboard service | A container importing the library, serving HTTP | the routing and the answers, not the server |
| Agent client | What agents import to call it | `BoardClient` and `AsyncBoardClient` |
| Database | One primary you already run | no |
| Retention | Deciding when a finished run's record goes | `store.delete`, when you call it |
| Agents | Independent deployments | no |

The package ships `PostgresStore` and `MongoStore` for a deployment and `SqliteStore` for one machine, all satisfying the `BoardStore` protocol. Against any other database the eight methods are yours to write: four read, three write, one removes a board, and every rule they are held to maps onto ordinary primitives. [Storage](storage.md) covers what each has to guarantee.

## What is durable and what is not

A board adapter makes the **record** durable. It does not make the **run** durable, and the difference decides how the service is deployed.

| Held where | What |
| --- | --- |
| The database, through the board | Regions, contributions, premise values and versions, the sequence |
| The process, in `Control` | The agent registry, outstanding notifications, the audit, the idle and wall-clock timers |
| The process, in `HttpNotifier` | Notifications queued but not yet sent |

A `Control` lives in one process. A second replica holding its own `Control` for the same board knows no agent the first registered, owes no notification the first dispatched, and measures silence from its own start. Losing the replica that holds one ends that run: the record survives and the run does not resume.

So one board is served by one `Control` in one process at a time. The service is scaled by putting different boards on different replicas, and routed to by board identifier; it is not scaled by putting more replicas behind one board. A replica that dies is replaced, and the run it held is started again with `attach_model`, which opens a run over the record the dead replica left rather than declaring a board that already exists.

Making the run itself durable, so that a replacement resumes rather than restarts, means putting the registry, the outstanding notifications, and the deadlines in the database alongside the record. That is not in the library today, and `docs/design/durable-runs.md` in the repository sets out what it would take.

A notification lost with the process usually costs nothing, since a notification carries no values and the next one covers the range a lost one would have covered. It costs something when the lost one is the last, and the run then waits until its idle limit closes it.

## The path a call takes

Agents never touch the database and never import the library. They call the service, and the service calls the library.

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
| The paths, the methods, and the status codes | `blackboard.wire` |
| Matching a request and answering it | `blackboard.server` |
| Building the request and reading the answer | `blackboard.agent` |
| Admitting the write, ordering it, storing it | `blackboard` |
| Sending the notification, retrying it, reporting a failure | `blackboard.delivery` |

Serialisation is not on the service's side of that line, because content already crosses every board as JSON, so a contribution has the same form on the wire that it has in the record.

**Delivery.** `HttpNotifier.to(url)` is the callable an `Agent` is created with. It queues the notification and returns, so the agent that wrote is not made to wait, and it sends on a worker of that agent's own, so one agent that is slow delays nobody else. [Notify agents over HTTP](../guides/notifying-agents.md) covers the retry policy and what a failure looks like.

**Idempotency.** An HTTP retry must not append a contribution twice, so the client attaches a key and the service deduplicates on it.

## What does not change

Regions, admission, subscription, notification, and the three outcomes behave identically whether the caller is in the same process or across a network, because the control component only ever learns that an agent stopped.
