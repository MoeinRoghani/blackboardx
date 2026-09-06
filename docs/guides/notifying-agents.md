# Notify agents over HTTP

An agent that runs in the same process as the blackboard is reached by a
function call. An agent that runs as its own service is reached over the
network, and the control component knows nothing about networks.

`HttpNotifier` is what knows. It offers the same sending two ways, and which
one to use is decided by one question: does more than one process serve this
board?

| | `reach`, with an `address` on the agent | `to`, as the agent's `notify` |
| --- | --- | --- |
| Where the address lives | The run, so every process reads it | The lane, so this process alone has it |
| Which process can deliver | Any that was given the transport | The one that opened the lane |
| When the writer's thread is free | After the send | At once, because the send is queued |
| Retries | The relay, on its next pass | The lane, four attempts by default, then the relay |
| To close | Nothing | Each lane, and the notifier |

Deployed behind several replicas, use `reach`. A run inside one process can use
either, and a lane keeps the writer's thread off the network.

## Wiring it up

```
pip install 'blackboardx[notifier]'
```

```python
from datetime import timedelta

from blackboard import Agent, Level, Premise, RunLimits, create_model
from blackboard.delivery import HttpNotifier

with HttpNotifier() as notifier:
    model = create_model(
        board_id=board_id,
        store=store,
        regions=[Level("signals"), Level("findings"), Premise("severity")],
        premises={"severity": "unknown"},
        agents=[
            Agent(
                name="triage",
                subscribes_to={"signals"},
                address="https://triage.internal/notify",
            ),
            Agent(
                name="correlator",
                subscribes_to={"findings"},
                address="https://correlator.internal/notify",
            ),
        ],
        limits=RunLimits(wall_clock=timedelta(minutes=30), idle=timedelta(minutes=2)),
        reach=notifier.reach,
    )
    model.control.wait_closed()
```

Each address is written to the run, so a second replica serving the same board
delivers to the same agents without being told about them. `reach` sends on
the calling thread and does not retry: the row the write recorded is what
retries, on the next pass of `Control.relay`.

## A lane, where the writer must not wait

`notifier.to(url)` returns a lane, which is the `notify` callable an `Agent`
takes. It queues the notification and returns, so the agent that wrote is not
made to wait, and it sends on a worker of its own with the retry policy below.

A notifier whose lanes are used takes the store:

```python
with HttpNotifier(store=store) as notifier:
    ...
```

A lane returns before the notification is on the wire, so the control component
cannot know the send happened, and the lane is what records it. Without the
store the row is cleared when the lane accepts the notification, and one the
lane never sends is lost. `reach` needs no store, because it sends where it is
called and what marks the row can see that it returned.

```python
Agent(
    name="triage",
    subscribes_to={"signals"},
    notify=notifier.to("https://triage.internal/notify"),
    address="https://triage.internal/notify",
)
```

Declaring both is the deployment that wants each: the callable is the faster
path in the process that holds it, and the address is what any other process
uses. A lane belongs to the process that opened it, so an agent declared with
a lane alone is reachable from that process and no other.

## Closing a lane and closing the notifier

Keep the notifier open for as long as the runs that use it are open. Closing it
stops
every worker, so a notifier closed while a run is live leaves that run's
agents unreachable.

Close each run's lanes when that run ends. `to` returns a `Lane`, which is the
callable that an `Agent` takes, and a `Lane` can also be closed on its own:

```python
lanes = [notifier.to(url) for url in addresses]
...
for lane in lanes:
    lane.close()
```

A notifier serving many runs over its life would otherwise hold a queue and a
thread for every agent of every run it has ever served. Closing a lane reports
whatever it still held, and returns once it has reported it, so nothing is left
queued behind a closed lane. Closing the notifier still closes any lane you did
not close.

`close_timeout` on the notifier, ten seconds by default, bounds both
closings. Closing the notifier spends it across every lane at once rather
than on each in turn, so five lanes parked in a retry cost one timeout
between them. Closing a single lane spends it on that lane, and does not cut
short any other lane's retries. Whatever a lane still holds when the bound
passes is
reported as undelivered before `close` returns.

`to` on a notifier that has already closed raises `RuntimeError`. A run opened
after that point needs a notifier of its own.

## What sending inline costs

The control component sends on the thread of whichever agent just wrote, one
agent after another, before returning. Five agents at a fifth of a second each
cost that writer a full second for a write that took microseconds, and one
agent whose endpoint hangs costs it the whole timeout. That is what `reach`
does, because it sends where it is called.

A queue moves all of it off the writer's thread: a lane puts the notification
down and returns. What it costs in exchange is that the queue belongs to this
process, which is the trade the table at the top of this page compares.

## Why every agent gets its own lane

Each call to `to` opens a queue and a worker of its own. Agents are therefore
reached at the same time, and an agent that is slow, retrying, or down holds up
only its own queue.

Call `to` once per agent, even when two agents answer at the same address.
Two agents sharing one callable share one queue and take turns.

## What happens when a delivery fails

This section is about a lane. A send through `reach` raises to its caller,
which is the relay: the row stays unsent, the failure is logged at `WARNING`
on the `blackboard` logger, and the next pass tries again. Nothing below
applies to it.

The notifier tries again. `attempts` counts every call to the transport, so
the default of 4 is one send and three retries, and `backoff` decides the
wait between them. The default backoff doubles that wait each time and draws
from the range between half of the doubled wait and all of it, so agents that
failed together do not
all return at the same moment. A server that answered with `Retry-After` gets
the delay it asked for, capped at thirty seconds. Only the seconds form of
that header is read, and a date in it is ignored in favour of the doubling.

Any answer other than a 2xx, 408, 425, 429, or a 5xx is a refusal rather than a
failure. The agent will answer the same way next time, so the
notifier reports it without retrying.

Everything that the notifier gives up on is logged at `ERROR` on the
`blackboard.delivery` logger, naming the agent, the notification, and how
many attempts it took. Pass `on_failure` to receive the same thing as an
`Undelivered` object, which names the address, the agent, the notification,
how many times the transport was called, and the error that stopped it:

```python
def missed(undelivered: Undelivered) -> None:
    metrics.increment("blackboard.undelivered", agent=undelivered.agent)


notifier = HttpNotifier(on_failure=missed)
```

That handler runs on the failing agent's lane thread. A notification handed to
a lane that has already closed is reported on the thread that handed it over,
with `attempts` at zero, and the write that woke the agent still returns. Keep
the handler short, and do not write to the board from it.

A notification that never lands is not the end of the run. The agent has not
acknowledged, so the run's idle limit still applies and the outcome names
that agent as unfinished.

## What the queue loses, and what recovers it

The queue is in memory. A process that stops loses whatever had not been
sent, and `close` reports what it abandons through `on_failure`.

The intent is not lost with it. A write records one row for each agent that
should hear of it, in the same transaction as the contribution, and the lane
clears that row only once it has sent. So a notification abandoned here is
still owed, and `Control.relay` delivers it, from this process when it comes
back or from any other replica that can reach the agent.

That is what the store on the notifier buys, and the reason to pass it. A
notifier without one leaves the marking to the control component, which sees
the lane accept the notification and cannot see what became of it, so a
notification lost in the queue is lost outright.

Delivery is therefore at least once. A row is marked only after the send
returns, so a process that sends and stops before marking sends again, and a
notification may arrive twice. That costs nothing: a notification carries no
values, the agent reads the board either way, and cumulative acknowledgment
absorbs the extra identifier.

## Sending over something else

`Transport` has two methods, `send` and `close`. Implement it to send over a
message broker, to add a header that every request needs, or to record what
would
have been sent:

```python
class Recording:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def send(self, url: str, body: dict) -> None:
        self.sent.append((url, body))

    def close(self) -> None:
        pass


notifier = HttpNotifier(transport=Recording())
```

Raise `DeliveryRefused` from `send` for something that the agent will refuse
again, and `DeliveryFailed`, or any other exception, for something that another
attempt might land. Both descend from `BlackboardError`, and one does not
descend from the other, so an `except DeliveryFailed` does not catch a refusal.
`DeliveryFailed` takes `retry_after` when the far side named a delay.

A transport you supply is yours to close. The notifier builds an
`HttpxTransport` when it is given no transport, and closes that transport when
the notifier closes.

The protocol is in the base install. Only `HttpxTransport`, the
implementation that uses `httpx`, needs the `notifier` extra.

## What the agent receives

A JSON object, posted to the address you gave `to`:

```json
{
  "board_id": "0f1d...",
  "notification_id": 12,
  "agent": "triage",
  "from_sequence": 4,
  "to_sequence": 9,
  "regions": ["signals"]
}
```

`blackboard.wire.NotificationBody.from_json` decodes it, and ignores fields
that a later version adds. It refuses a body that leaves out `from_sequence` or
`to_sequence`, because an absent `from_sequence` would decode as zero and send
the agent through the
whole level. Any 2xx means the agent took it.
[Write an agent](writing-an-agent.md) covers what the agent does next.
