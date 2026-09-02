# Write an agent

An agent's work starts when the library notifies it and finishes when the agent reports that it has stopped.

1. It receives a notification.
2. It reads whatever it wants from the board.
3. It decides whether it has anything to add.
4. It writes each thing it wants to add.
5. It acknowledges.

The library asks nothing of an agent outside these steps. Steps 2 and 3 are invisible to it: reads go straight to the board, and nothing reports what the agent concluded. It learns what the agent wrote, because it sequenced those writes itself. Deciding to add nothing is an ordinary outcome and skips step 4.

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

Omit `subscribes_to` and the agent is woken by every premise and by no level. Name a level and a contribution to it wakes the agent, which is how one agent's finding starts another's work.

Omit `writes_to` and every level is permitted. Name one and a write to any other level comes back `Rejected` with the cause `NOT_PERMITTED`.

Naming a level that was never declared is a different failure, and it is caught earlier: `register_agent` raises `UndeclaredRegionError` rather than letting the agent register with a permission it can never use.

An agent is never woken by its own write.

## Joining a run

Name the agent when the run is created, which is how agents normally arrive.

```python
model = create_model(..., agents=[Agent(name="ocp", notify=investigate)])
```

An agent that joins a run already under way registers itself instead, and is woken the same way.

```python
model.control.register_agent(Agent(name="netops", notify=investigate))
```

Either way the agent is woken immediately, covering every subscribed region that already holds something, because an agent that has just joined is out of date with the whole board.

## Coming back after a restart

An agent that restarts registers again under the same name. That replaces its declaration, including its callback address and its subscriptions, so a redeployed agent that moved to a new address is reached at the new one.

Its cursor survives, because the agent has not forgotten what it acknowledged. Whatever notification the old process was still holding is discarded, and one fresh notification covers everything since that cursor. One notification says what several would have said, because a notification carries no values.

Naming the same agent twice in one roster at creation is refused, since that is one list written at one moment and a repeat there is a mistake rather than a return.

The callback therefore runs **before** `create_model` or `register_agent` returns. A callback that needs the model must be given it another way, because the call has not returned yet.

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

The callback runs on the thread that dispatched. Nothing requires the agent to work there.

```python
def hand_off(notification):
    executor.submit(do_the_work, notification)  # return at once
```

Acknowledgment is everything the control component learns about how an agent ran. It records what the agent wrote, because it sequenced those writes itself, and it learns nothing about how long the work took, whether it succeeded, or where it happened. It never kills an agent, and an agent takes as long as it takes.

## What acknowledging means

Acknowledging a notification also acknowledges every notification you were sent whose range ends at or before that one's. The cursor is cumulative, so answering the widest range answered the narrower ones inside it, and an agent that reads to the board's end can acknowledge only the last identifier it holds.

That also covers the notification you never received. A delivery that raised is suppressed, so that one agent's failure does not reach an unrelated writer, which means you cannot acknowledge it by name. Your next acknowledgment covers it.

It means the agent has stopped working on that notification. It does not mean the agent found anything, and it does not mean the agent will not be woken again.

When a run closes, it names every agent still holding a notification as unfinished.

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

A client is bound to one board and one agent name, so no method takes either
and none can be given the wrong one.

## Writing the body once

`BoardClient` and `Control.as_agent` both satisfy `AgentBoard`, the protocol
one board looks like to one agent. Write the body against that and it runs
either way with no edit:

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

`AgentBoard` carries the four reads `BoardReader` has and the three writes
`Control` has, each without the agent's own name, because the object already
holds it. `BoardClient` also satisfies `BoardReader` on its own, so an
admission rule or a termination predicate written against that protocol reads
a remote board too.

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

Neither client is written in terms of the other. A synchronous method that
starts an event loop per call throws away the connection pool, so both are
real, and the request they build is one piece of shared code.

### What comes back, and what is raised

A write answers the way `Control.write` answers, with `Written` or
`Rejected`, and setting a premise adds `Conflict`. Those are answers rather
than faults: the same request sent again gets the same one. A `Conflict` is
answered by reading the premise and deciding again.

```python
outcome = board.set_premise("severity", "high", expected_version=3)
if isinstance(outcome, Conflict):
    current = board.read_premise("severity")
    outcome = board.set_premise("severity", decide(current.value), current.version)
```

Everything else is raised, and where the blackboard has an exception for it
the client raises that one, so `except UndeclaredRegionError` catches the
same mistake in either deployment.

| Raised | When |
| --- | --- |
| `UndeclaredRegionError` | No region by that name |
| `RegionKindError` | That name is a premise, and you read it as a level |
| `UnsetPremiseError` | A premise declared without an opening value |
| `UnknownNotificationError` | Acknowledging one this agent was never sent |
| `UnknownBoardError` | The blackboard holds no run for that board |
| `Unreachable` | It could not be reached, or kept answering 5xx |
| `ProtocolError` | The two halves are out of step |

### What is attempted again

A read and an acknowledgment are attempted again when the blackboard cannot
be reached or answers 5xx. Reading twice returns the same thing, and
acknowledging a notification that is no longer outstanding changes nothing.
The wait between attempts doubles and is drawn from the range between half of
it and all of it, and a blackboard that sent `Retry-After` gets the delay it
asked for.

**A write is attempted again only when it carries a key.** A request that
timed out may still have been received, and a contribution appended twice is
not the same board, so a write with nothing to identify it raises
`Unreachable` and what to do next is the agent's to decide.

Give the write a key and the blackboard writes it once, however many times it
arrives:

```python
board.write("findings", conclusion, idempotency_key=f"{notification.notification_id}-1")
```

The client then retries that write like a read. If the first attempt landed
and its answer was lost, the second comes back as the first one's result with
`repeated` set, so the agent sees one write and the board holds one row.

Name the key after work the agent can name again after a restart, rather than
a fresh random string each time. The notification's id and a counter is
usually enough, and a key that survives a restart deduplicates a restart.

A key names one write on one board. Sending it for a different region is a
mistake rather than a retry, and comes back as
`Rejected` with the cause `idempotency_key_reused`. Sending it again for the
region it does name returns the first write whatever content goes with it, so
a retry sends what it sent before.

Retrying a keyed write needs a blackboard at 0.8 or later. An older one takes
the key and ignores it, and a retry against it writes twice.

### Reading a whole level

`read_level(level)` reads to the end, following the blackboard's pages. Give
it a `limit` to ask for one page instead, and continue from one past the last
sequence you received.

```python
page = board.read_level("signals", from_sequence, limit=100)
next_from = page[-1].sequence + 1 if page else from_sequence
```

A sequence number is the cursor because an offset would shift when a
concurrent write lands.

### Authentication and connections

The client opens a plain `httpx` client unless you give it one. Give it one
configured with whatever your deployment needs, and keep it for the life of
the service rather than building one per notification:

```python
session = httpx.Client(headers={"authorization": token}, timeout=10.0)
board = BoardClient(base_url=..., board_id=..., agent="triage", http_client=session)
```

A client you supply is yours to close. One the library opened is closed with
the `BoardClient`.
