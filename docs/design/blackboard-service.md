# The blackboard service

**Status: a design, not a thing that exists.** The library ships the board and its adapters; the service, the agent client, and the durable run this document describes are not built. What the library holds in the process today, and what that means for replicas, is in [Running as a service](../concepts/service.md).

A deployment that holds blackboards for agents which run as separate services. It owns the only connection to the database, imports `blackboardx`, and exposes the blackboard over HTTP. Agents never reach the database and never import the library.

## The parts

| Part | What it is | Owned by | Run by |
| --- | --- | --- | --- |
| `blackboardx` | A pip package: the board, the control component, the storage protocol | This repository | Imported, never run alone |
| Storage adapter | An implementation of that package's protocol for one database | The application | Imported by the service |
| Blackboard service | A container that imports the library and serves HTTP | This repository | Kubernetes, several pods |
| Agent client | A small package agents import, wrapping the HTTP surface | This repository | Imported by agents |
| Database | One PostgreSQL primary | The platform | Already running |
| Agents | Independent services | Their own teams | Kubernetes |

A pod would keep nothing between requests: it would read what a request needs from the database and write back before answering, so any pod serves any blackboard and losing a pod loses no work. That is the point of the `agents`, `notifications`, and `audit` tables below, and it is the part that does not exist. Today the control component holds all three in the process, so one blackboard is served by one process at a time.

## What the database holds

| Table | Holds |
| --- | --- |
| `blackboards` | One row per blackboard: status, outcome, wall clock limit, idle limit |
| `regions` | The named regions of a blackboard and their kind |
| `premises` | The current value and version of each premise |
| `contributions` | Every contribution, with its sequence number and region |
| `agents` | Each registered agent: callback address, level subscriptions, write permissions |
| `notifications` | Every notification issued, delivered or not, acknowledged or not |
| `audit` | Every event, in the order each occurred |

Two columns carry the model's reconciliation rules. `contributions.sequence` is taken by incrementing a counter row inside the writing transaction, not from a database sequence: a sequence does not roll back, and a gap is a hole in a record whose numbers are addresses. `premises.version` makes a premise write a compare and set, where an update matching no row is the conflict the model returns. The shipped adapters already work this way; see [Storage](../concepts/storage.md).

## The HTTP surface

| Call | From | Purpose |
| --- | --- | --- |
| `POST /blackboards` | Any caller | Create a blackboard |
| `GET /blackboards/{id}` | Anyone | Read regions, premise values, agents, sequence |
| `GET /blackboards/{id}?from=N` | Anyone | Everything written after sequence N |
| `GET /blackboards/{id}?levels=a,b` | Anyone | Those levels in full |
| `POST /blackboards/{id}/agents` | An agent | Premise or re-premise |
| `POST /blackboards/{id}/writes` | An agent or any component | Contribute, acknowledge, or both |
| `POST /blackboards/{id}/premises/{name}` | An agent or any component | Replace a premise value under its version |
| `POST /blackboards/{id}/close` | Any caller | End the blackboard |
| `POST {callback_url}` | The service | Notify one agent |

Reads never pass through admission, cost nothing, and cannot be refused.

## Step 0. The service starts

A pod reads `DATABASE_URL`, `SWEEP_INTERVAL`, `DELIVERY_TIMEOUT` and `PORT` from its configuration, opens the connection pool, starts the HTTP listener, and starts the sweep loop. A pod that cannot reach the database does not start.

Nothing is created and no schema is checked. Migrations run as a deployment step.

Every pod does this identically and holds no blackboard.

## Step 1. A blackboard is created

```http
POST /blackboards
{
  "name": "incident-4471",
  "regions": [
    {"name": "service",     "kind": "premise"},
    {"name": "window",      "kind": "premise"},
    {"name": "namespace",   "kind": "premise"},
    {"name": "application", "kind": "level"},
    {"name": "platform",    "kind": "level"}
  ],
  "premises": {
    "service":   "checkout-api",
    "window":    ["2026-08-20T09:00Z", "2026-08-20T09:40Z"],
    "namespace": ["prod-checkout"]
  },
  "wall_clock": "1h",
  "idle": "10m"
}
```

The caller declares the regions it wants. There are no preset kinds of blackboard and no stored configuration.

The opening values write every declared premise once. A body naming a premise that was not declared, or omitting one that was, is refused.

No agent is named, because an agent registers itself and cannot be known at this moment.

| Written | Rows |
| --- | --- |
| `blackboards` | 1 |
| `regions` | 5 |
| `premises` | 3, each at version 1 |
| `audit` | 3 |

Nothing is notified, because no agent exists.

## Step 2. An agent registers

An agent reads the blackboard to learn which regions exist, then premises.

```http
POST /blackboards/incident-4471/agents
{
  "name": "ocp",
  "callback_url": "http://ocp.agents.svc/notify",
  "subscribes_to": ["window", "namespace", "application"],
  "writes_to": ["platform"]
}
```

`subscribes_to` names the regions this agent wants to hear about, of either kind. Omitting it subscribes the agent to every premise, which is the common case, since a premise holds something the work was given and most agents compute from all of them. An agent that reads only some of the premises names those, and is not woken for the others.

`writes_to` names the levels the agent may write to. It is a permission rather than a subscription, so subscribing to three regions and writing to one is ordinary.

A name already registered updates that row rather than failing, because a pod restart re-premises. Anything outstanding for that agent is delivered again to the address it just supplied.

Registration names a region that was not declared, or reaches a closed blackboard, and is refused.

Registering issues one notification, because a newly registered agent is out of date with everything already on the board.

## Step 3. An agent is notified

```http
POST http://ocp.agents.svc/notify
{ "blackboard_id": "incident-4471", "notification_id": 7 }
```

A notification carries no values and no description of what moved. It states that the agent is out of date. The board is where the agent finds out what changed, and reading it costs nothing.

The agent answers immediately, before doing any work.

| Response | The service |
| --- | --- |
| `202` | Records delivery and moves on |
| Anything else, or no answer | Retries with backoff until `DELIVERY_TIMEOUT`, then records the agent unreachable |

Delivery is at least once. The same notification identifier may arrive more than once, and an agent ignores a repeat of one it has already taken.

Every change that concerns an agent produces its own notification. Notifications are not collapsed and are not held back while an agent is busy, because how much work an agent can carry at once is a fact about that agent, not about the board.

## Step 4. An agent contributes and acknowledges

After answering `202` the agent reads whatever it wants, does its own work, and returns on a fresh connection.

```http
POST /blackboards/incident-4471/writes
{
  "agent": "ocp",
  "writes": [
    { "level": "platform", "content": {...}, "idempotency_key": "ocp-a1b2" }
  ],
  "ack": 7
}
```

One call carries both facts, because a contribution and the end of one notification's work normally occur together and committing them separately leaves a window in which the contribution is on the board while the agent still appears to be working.

| Body | Meaning |
| --- | --- |
| `writes` present, `ack` present | Contributed and finished |
| `writes` empty, `ack` present | Nothing to add, and finished. An ordinary outcome |
| `writes` present, `ack` absent | Publishing early, still working |
| `writes` present, no notification at all | A component that was never woken, such as an operator tool |

Each write names its own level, so one call may contribute to several levels, and all of it commits together or none of it does.

A write is refused when the blackboard has closed, when the level is outside the agent's `writes_to`, or when the application's admission rule rejects it. A repeated `idempotency_key` returns the sequence number the first attempt received.

An accepted write to a level notifies every agent subscribed to that level except the agent that wrote it.

A premise is replaced through its own call, naming the version it expects to replace, and a write naming a version other than the current one fails and returns the current one. An accepted premise write notifies every agent except the writer.

## Step 5. The blackboard closes

| Cause | Outcome |
| --- | --- |
| A caller closed it | `Aborted` |
| The wall clock limit passed | `WallClockExpired` |
| Nothing happened for the idle limit | `Settled` |

The idle limit measures silence, meaning the time since the last write, premise write, registration, or acknowledgment. A read does not disturb it, so an agent polling the board cannot hold a blackboard open indefinitely.

A blackboard is not closed because nothing is outstanding at some instant. Agents are idle between notifications and they register at different times, so an instant of quiet is not the end of the work. Sustained silence is.

Every outcome carries the agents that did not finish, meaning those with an unacknowledged notification and those recorded unreachable. Why a blackboard ended and who failed to finish are separate facts, and a blackboard can settle normally while one agent never returned. A reader needs both, because a region nobody examined and a region examined with nothing in it are different states.

After closing, reads continue to work. Writes, premise writes, and registrations are refused.

## The sweep

Every pod runs the same loop on `SWEEP_INTERVAL`, taking rows with `FOR UPDATE SKIP LOCKED` so that several pods never act on the same row and none waits on another.

| Query | Action |
| --- | --- |
| Notifications never delivered, older than `DELIVERY_TIMEOUT` | Record the agent unreachable, stop retrying |
| Notifications never delivered, younger than that | Attempt delivery again |
| Blackboards past their wall clock limit | Close as `WallClockExpired` |
| Blackboards silent for their idle limit | Close as `Settled`, naming any agent that did not finish |

## Rules that hold throughout

A change that lands is always notified to every agent that should hear it. No counter and no limit suppresses a notification, because a blackboard whose changes reach nobody has stopped being a shared record.

Time is the only bound on a blackboard. The wall clock ends it and the idle limit ends it, and nothing else counts against it.

The board is the truth and a notification is a doorbell. An agent that receives one learns only that it is out of date.

Contributions are JSON, because they cross HTTP and land in a column. The application's admission rule is where that is enforced.
