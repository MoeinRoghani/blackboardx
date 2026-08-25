# Running as a service

The library is in-process. An application whose agents are separately deployed services puts one service in front of it, and that service is the only thing that imports the library and the only thing that reaches the database.

## The parts

| Part | What it is | Ours |
| --- | --- | --- |
| `blackboardx` | This package | yes |
| Storage adapter | A `BoardStore` implementation against your database | `PostgresBoard` or `MongoBoard`, or you write one |
| Blackboard service | A container importing the library, serving HTTP | yes |
| Agent client | A small package agents import, wrapping the HTTP calls | yes |
| Database | One primary you already run | no |
| Agents | Independent deployments | no |

The package ships `PostgresBoard` and `MongoBoard` for a deployment and `SqliteBoard` for one machine, all satisfying the `BoardStore` protocol. Against any other database the six methods are yours to write: three read, three write, and both reconciliation rules map onto ordinary primitives. [Storage](storage.md) covers what each has to guarantee.

A pod keeps nothing between requests. It reads what a request needs and writes back before answering, so any pod serves any blackboard and losing a pod loses no work. That is what a `BoardStore` against a shared database buys, and it is why `Control` names no concrete board type.

## The path a call takes

Agents never touch the database and never import the library. They call the service, and the service calls the library.

```
agent  ──HTTP──▶  blackboard service  ──▶  blackboardx  ──▶  database
   ▲                      │
   └──────notification────┘
```

## What the service adds

Two things the library deliberately does not have, because they are transport, not model. Serialisation is not among them: content crosses every board as JSON already, so what a contribution is on the wire is what it is in the record.

**Delivery.** The library hands the service a notification and a callback. Reaching a remote agent over HTTP, retrying, and deciding when an agent is unreachable belong to the service.

**Idempotency.** An HTTP retry must not append a contribution twice, so the client attaches a key and the service deduplicates on it.

## What does not change

The model. Regions, admission, subscription, notification, and the three outcomes behave identically whether the caller is in the same process or across a network, because the control component only ever learns that an agent stopped.
