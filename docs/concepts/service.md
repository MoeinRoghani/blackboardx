# Running as a service

The library is in-process. An application whose agents are separately deployed services puts one service in front of it, and that service is the only thing that imports the library and the only thing that reaches the database.

Read [what is durable and what is not](#what-is-durable-and-what-is-not) before deciding how many replicas that service runs.

## The parts

| Part | What it is | Ours |
| --- | --- | --- |
| `blackboardx` | This package | yes |
| Storage adapter | A `BoardStore` implementation against your database | `PostgresStore` or `MongoStore`, or you write one |
| Blackboard service | A container importing the library, serving HTTP | yes |
| Agent client | A small package agents import, wrapping the HTTP calls | yes |
| Database | One primary you already run | no |
| Agents | Independent deployments | no |

The package ships `PostgresStore` and `MongoStore` for a deployment and `SqliteStore` for one machine, all satisfying the `BoardStore` protocol. Against any other database the six methods are yours to write: three read, three write, and both reconciliation rules map onto ordinary primitives. [Storage](storage.md) covers what each has to guarantee.

## What is durable and what is not

A board adapter makes the **record** durable. It does not make the **run** durable, and the difference decides how the service is deployed.

| Held where | What |
| --- | --- |
| The database, through the board | Regions, contributions, premise values and versions, the sequence |
| The process, in `Control` | The agent registry, outstanding notifications, the audit, the idle and wall-clock timers |

A `Control` lives in one process. A second replica holding its own `Control` for the same board knows no agent the first registered, owes no notification the first dispatched, and measures silence from its own start. Losing the replica that holds one ends that run: the record survives and the run does not resume.

So one board is served by one `Control` in one process at a time. The service is scaled by putting different boards on different replicas, and routed to by board identifier; it is not scaled by putting more replicas behind one board. A replica that dies is replaced, and the run it held is started again against the record it left.

Making the run itself durable, so that a replacement resumes rather than restarts, means putting the registry, the outstanding notifications, and the deadlines in the database alongside the record. That is not in the library today.

## The path a call takes

Agents never touch the database and never import the library. They call the service, and the service calls the library.

```
agent  ──HTTP──▶  blackboard service  ──▶  blackboardx  ──▶  database
   ▲                      │
   └──────notification────┘
```

## What the service adds

The service adds two things the library leaves out, both of which belong to moving a message rather than to the model. Serialisation is not among them, because content already crosses every board as JSON, so a contribution has the same form on the wire that it has in the record.

**Delivery.** The library hands the service a notification and a callback. Reaching a remote agent over HTTP, retrying, and deciding when an agent is unreachable belong to the service.

**Idempotency.** An HTTP retry must not append a contribution twice, so the client attaches a key and the service deduplicates on it.

## What does not change

Regions, admission, subscription, notification, and the three outcomes behave identically whether the caller is in the same process or across a network, because the control component only ever learns that an agent stopped.
