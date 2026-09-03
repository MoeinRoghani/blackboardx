# Write an agent

An agent's work starts when the library notifies it and finishes when the agent
reports that it has stopped.

1. It receives a notification.
2. It reads whatever it wants from the board.
3. It decides what to add, or to add nothing.
4. It writes each thing it wants to add.
5. It acknowledges.

The library asks nothing of an agent outside these steps. Steps 2 and 3 are
invisible to the library: reads go straight to the board, and nothing reports
what the agent concluded. The library learns what the agent wrote, because it
sequenced those writes itself. Deciding to add nothing is an ordinary outcome
and skips step 4.

Step 3 is where the agent's expertise sits, and this page writes it as an
algorithm in the agent's own code. An agent that asks a language model instead
takes the same five steps and offers the board's reads and writes to the model
as tools it may call, which [Let a model decide](deciding-with-a-model.md)
covers.

## The smallest one

```python
from blackboard import Agent


def investigate(notification):
    window = model.reader.read_premise("window").value
    findings = look_for_trouble(window)  # your own work
    if findings:
        model.control.write("platform", {"findings": findings}, writer="ocp")
    model.control.ack(notification.notification_id, agent="ocp")


model.control.register_agent(Agent(name="ocp", notify=investigate))
```

## Declaring what wakes it

```python
Agent(
    name="ocp",
    notify=investigate,
    subscribes_to=["window", "namespace"],  # only these wake it
    writes_to=["platform"],  # it may write only here
)
```

Omit `subscribes_to` and the agent is woken by every premise; no level wakes
it. Name a level, and a contribution to that level wakes the agent, which is
how one agent's finding starts another's work.

Omit `writes_to` and every level is permitted. Name a level, and a write to any
other level comes back `Rejected` with the cause `NOT_PERMITTED`. The
permission is held against the writer's name, so it constrains writes made as
`ocp` and nothing else: a write under a name that nobody registered reaches any
declared level.

Naming a level that was never declared is a different failure, and it is caught
earlier: `register_agent` raises `UndeclaredRegionError` rather than letting
the agent register with a permission it can never use. Naming a premise in
`writes_to` raises `RegionKindError`, because only a level takes a
contribution.

An agent is never woken by its own write.

## Joining a run

Name the agent when the run is created, which is how agents normally arrive.

```python
model = create_model(..., agents=[Agent(name="ocp", notify=investigate)])
```

An agent that joins a run already under way registers itself instead, and is
woken the same way.

```python
model.control.register_agent(Agent(name="netops", notify=investigate))
```

In both cases the agent is woken immediately, covering every subscribed region
that already holds something, because an agent that has just joined is out of
date with the whole board. A subscribed premise carrying a batch window is the
exception: registering schedules the notification a window away, so an agent
with nothing else pending is woken when that window passes.

## Coming back after a restart

An agent that restarts registers again under the same name. Registering again
replaces its declaration, including its callback address and its subscriptions,
so a redeployed agent that moved to a new address is reached at the new one.

Its cursor survives, because the control component has not forgotten what the
agent acknowledged. Whatever notification the old process was still holding is
discarded, and one fresh notification covers everything since that cursor,
because a notification carries no values.

Naming the same agent twice in one roster at creation raises
`DuplicateAgentError`, since that is one list written at one moment and a
repeat there is a mistake rather than an agent coming back after a restart.

Registering wakes the agent, so the callback runs **before** `create_model` or
`register_agent` returns. A callback that needs the model must be given it
another way, because the call has not returned yet.

```python
holder = []


def investigate(notification):
    model = holder[0]
    ...


model = create_model(...)
holder.append(model)  # before registering
model.control.register_agent(Agent(name="ocp", notify=investigate))
```

## Doing the work elsewhere

The callback runs on the thread that dispatched the notification. Nothing
requires the agent to work there.

```python
def hand_off(notification):
    executor.submit(do_the_work, notification)  # return at once
```

Acknowledgment is everything the control component learns about how an agent
ran. The control component records what the agent wrote, because it sequenced
those writes itself, and it learns nothing about how long the work took, its
success or failure, or where it happened. It never kills an agent.

## What acknowledging means

Acknowledging a notification also acknowledges every notification you were sent
whose range ends at or before that notification's range. The cursor is
cumulative, so answering the widest range answers the narrower ones inside it,
and an agent that reads to the board's end has to acknowledge only the last
identifier it holds.

The cumulative cursor also covers the notification you never received. You
cannot acknowledge by name a notification you never received. A delivery that
raised an exception is suppressed, so one agent's failure does not reach an
unrelated writer, and the notification that it carried never reaches you. Your
next acknowledgment covers it.

Acknowledging means the agent has stopped working on that notification. It does
not mean the agent found anything, and it does not mean the agent will not be
woken again.

A run that closes on silence or on the wall clock names every agent still
holding a notification in its outcome's `unfinished`. A run that a caller
aborted leaves `unfinished` empty, because `abort` closes the run without
collecting the agents still holding one.

## An agent in its own service

Everything above is an agent in the same process as the blackboard, reading
through `model.reader` and writing through `model.control`. An agent deployed
on its own does the same five steps against the same names, over HTTP.

```
pip install 'blackboardx[agent]'
```

```python
from blackboard.agent import BoardClient
from blackboard.wire import NotificationBody


@app.post("/notify")
def notify(body: dict):
    notification = NotificationBody.from_json(body)
    with BoardClient(
        base_url="https://blackboard.internal/v1",
        board_id=notification.board_id,
        agent="triage",
    ) as board:
        window = board.read_premise("window").value
        signals = board.read_level("signals", notification.from_sequence)
        for signal in signals:
            board.write("findings", investigate(signal, window))
        board.ack(notification.notification_id)
    return "", 204
```

A client is bound to one board and one agent name, so no method takes the board
or the agent name, and no method can be given the wrong board or the wrong
name.

`NotificationBody.from_json` raises `wire.WireError` on a body that is not an
object, or that leaves out `board_id`, `notification_id`, `agent`,
`from_sequence` or `to_sequence`. `WireError`'s base is `Exception` rather than
`BlackboardError`, so an `except BlackboardError` around the route does not
catch it.

## Writing the body once

`BoardClient` and `Control.as_agent` both satisfy `AgentBoard`, the protocol
that one board looks like to one agent. Write the body against `AgentBoard`,
and the body runs in both deployments with no edit:

```python
from blackboard import AgentBoard


def investigate(board: AgentBoard, from_sequence: int) -> None:
    severity = board.read_premise("severity")
    for signal in board.read_level("signals", from_sequence):
        board.write("findings", conclude(signal, severity.value))
```

In process, ask the control component for the board as that agent sees it:

```python
investigate(model.control.as_agent("triage"), notification.from_sequence)
```

Deployed on its own, hand it the client:

```python
with BoardClient(base_url=..., board_id=..., agent="triage") as board:
    investigate(board, notification.from_sequence)
```

`AgentBoard` carries the four reads that `BoardReader` has and the three writes
that `Control` has, each without the agent's own name, because the object
already holds it. It also carries a `board_id` property for a body that has to
name its own board. `BoardClient` also satisfies `BoardReader` on its own, so
an admission
rule or a termination predicate written against that protocol reads a remote
board too.

`as_agent` registers nothing. Registering decides what wakes an agent;
`as_agent` decides the name that agent's writes carry, so it serves a writer
that never registered as well as one that did.

Answer the notification before you acknowledge. Acknowledging is what tells
the run this agent has stopped, and [what acknowledging
means](#what-acknowledging-means) covers the rest.

### Async

`AsyncBoardClient` has the same methods with `await` in front of them.

```python
async with AsyncBoardClient(
    base_url="https://blackboard.internal/v1",
    board_id=notification.board_id,
    agent="triage",
) as board:
    signals = await board.read_level("signals", notification.from_sequence)
```

The two clients are written separately, not one in terms of the other. A
synchronous method that
starts an event loop per call throws away the connection pool, so each is
implemented on its own, and the request they build is one piece of shared code.

### What comes back, and what is raised

A write answers the way `Control.write` answers, with `Written` or
`Rejected`, and setting a premise adds `Conflict`. Each of those is a decision
that the run made about a write that it understood, so the caller handles the
decision where it made the call. A `Conflict` is answered by reading the
premise and
deciding again.

```python
outcome = board.set_premise("severity", "high", expected_version=3)
if isinstance(outcome, Conflict):
    current = board.read_premise("severity")
    outcome = board.set_premise("severity", decide(current.value), current.version)
```

Everything else is raised. A region name and an idempotency key are the
caller's to get right, so a wrong name or a wrong key raises an exception
rather than answering, and it raises the same exception whichever deployment
the body runs in. Where the
blackboard has an exception for a condition, the client raises that one.

| Raised | When | Where |
| --- | --- | --- |
| `UndeclaredRegionError` | No region by that name | Either deployment |
| `RegionKindError` | That name is a premise, and the call takes a level | Either deployment |
| `IdempotencyKeyError` | A key that already named one region, sent naming another | Either deployment |
| `UnsetPremiseError` | A premise declared without an opening value | Either deployment |
| `UnknownNotificationError` | Acknowledging one this agent was never sent | Either deployment |
| `UnknownBoardError` | The blackboard holds no run for that board, and no record to answer a read from | Over HTTP |
| `Unreachable` | It could not be reached, or kept answering 5xx | Over HTTP |
| `ProtocolError` | The two halves are out of step | Over HTTP |

`RejectionCause` has three members, `ADMISSION`, `NOT_PERMITTED` and
`RUN_CLOSED`, and a `Rejected` never carries anything else.

### What is attempted again

A read and an acknowledgment are attempted again when the blackboard cannot
be reached, or answers 408, 425, 429, or any 5xx. Reading twice returns the
same thing, and acknowledging a notification that is no longer outstanding
changes nothing. The wait between attempts doubles, and each wait is drawn from
the range between half of that wait and all of it, and a blackboard that sent
`Retry-After` gets the delay it asked for, up to thirty seconds.

**A write is attempted again only when it carries a key.** A request that
timed out may still have been received, and a contribution appended twice does
not leave the same board, so a write with nothing to identify it raises
`Unreachable` and what to do next is the agent's to decide.

Give the write a key and the blackboard writes it once, however many times it
arrives:

```python
board.write("findings", conclusion, idempotency_key=f"{notification.notification_id}-1")
```

The client then retries that write like a read. If the first attempt landed
and its answer was lost, the second attempt comes back as the first attempt's
result with `repeated` set, so the agent sees one write and the board holds one
row.

Name the key after work that the agent can name again after a restart, rather
than a fresh random string each time. The notification's id and a counter is
usually enough.

A key names one write on one board. Sending it for a different region is a
mistake rather than a retry, and raises `IdempotencyKeyError`. Over HTTP the
blackboard answers 409 naming the key and the client raises from that, so the
in-process body and the deployed one meet the same exception. Sending the key
again for the region it does name returns the first write, whatever content
goes with it.

Retrying a keyed write needs a blackboard at 0.8 or later. An older blackboard
takes the key and ignores it, and a retry against it writes twice.

### Reading a whole level

`read_level(level)` reads to the end, following the blackboard's pages. Give
it a `limit` to ask for one page instead, and continue from one past the last
sequence you received.

```python
page = board.read_level("signals", from_sequence, limit=100)
next_from = page[-1].sequence + 1 if page else from_sequence
```

One request to a blackboard answers with at most 100 contributions when it
names no limit, and at most 1000, whatever limit it names. The cap is silent: a
`limit` of 5000 comes back with 1000 and the list alone does not say it was
cut. In process there is no cap, and `read_level` with no limit returns the
whole level in one call.

A sequence number is the cursor because an offset would shift when a
concurrent write lands.

### Authentication and connections

The client opens a plain `httpx` client unless you give it one. Give it a
client configured with whatever your deployment needs, and keep it for the life
of
the service rather than building one per notification:

```python
session = httpx.Client(headers={"authorization": token}, timeout=10.0)
board = BoardClient(base_url=..., board_id=..., agent="triage", http_client=session)
```

A client you supply is yours to close. A client that the library opened is
closed with the `BoardClient`.
