# Notify agents over HTTP

An agent that runs in the same process as the blackboard is reached by a
function call. An agent that runs as its own service is reached over the
network, and the control component knows nothing about networks: it calls
`Agent.notify` and expects it to return.

`HttpNotifier` supplies that callable. It puts the notification on a queue,
returns, and a worker sends it.

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
                notify=notifier.to("https://triage.internal/notify"),
            ),
            Agent(
                name="correlator",
                subscribes_to={"findings"},
                notify=notifier.to("https://correlator.internal/notify"),
            ),
        ],
        limits=RunLimits(wall_clock=timedelta(minutes=30), idle=timedelta(minutes=2)),
    )
    model.control.wait_closed()
```

Keep the notifier open for as long as the runs that use it. Closing it stops
every worker, so a notifier closed while a run is live leaves that run's
agents unreachable.

Close each run's lanes when that run ends. `to` returns a `Lane`, which is the
callable an `Agent` takes and can also be closed on its own:

```python
lanes = [notifier.to(url) for url in addresses]
...
for lane in lanes:
    lane.close()
```

A notifier serving many runs over its life would otherwise hold a queue and a
thread for every agent of every run it has ever served. Closing a lane reports
whatever it still held, and returns once it has, so nothing is left queued
behind a closed lane. Closing the notifier still closes any lane you did not.

`close_timeout` on the notifier, ten seconds by default, bounds both
closings. Closing the notifier spends it across every lane at once rather
than on each in turn, so five lanes parked in a retry cost one timeout
between them. Closing a single lane spends it on that lane, and cuts short no
other lane's retries. Whatever a lane still holds when the bound passes is
reported as undelivered before `close` returns.

`to` on a notifier that has already closed raises `RuntimeError`. A run opened
after that point needs a notifier of its own.

## Why the writer does not wait

Without a queue, the control component sends on the thread of whichever
agent just wrote, one agent after another, before returning. Five agents at a
fifth of a second each cost that writer a full second for a write that took
microseconds, and one agent whose endpoint hangs costs it the whole timeout.

Queueing moves all of that off the writer's thread. `notify` puts the
notification down and returns, and the writer carries on.

## Why every agent gets its own lane

Each call to `to` opens a queue and a worker of its own. Agents are therefore
reached at the same time, and an agent that is slow, retrying, or down holds
up nothing but its own queue.

Call `to` once per agent, even when two agents answer at the same address.
Two agents sharing one callable share one queue and take turns.

## What happens when a delivery fails

The notifier tries again. `attempts` counts every call to the transport, so
the default of 4 is one send and three retries, and `backoff` decides the
wait between them. The default doubles that wait each time and draws from the
range between half of it and all of it, so agents that failed together do not
all return at the same moment. A server that answered with `Retry-After` gets
the delay it asked for, capped at thirty seconds. Only the seconds form of
that header is read, and a date in it is ignored in favour of the doubling.

An answer that is not a 2xx, and is not 408, 425, 429, or a 5xx, is a refusal
rather than a failure. The agent will answer the same way next time, so the
notifier reports it without retrying.

Everything the notifier gives up on is logged at `ERROR` on the
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

## What the queue does not survive

The queue is in memory. A process that stops loses whatever had not been
sent.

That usually costs nothing, because a notification carries no values: it says
a range changed, and the next one covers the range a lost one would have
covered. It costs something when the lost notification is the last one, since
no later notification arrives to cover it, and the run then waits until its
idle limit closes it.

## Sending over something else

`Transport` is two methods, `send` and `close`. Implement it to send over a
message broker, to add a header every request needs, or to record what would
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

Raise `DeliveryRefused` from `send` for something the agent will refuse
again, and `DeliveryFailed`, or any other exception, for something another
attempt might land. Both descend from `BlackboardError` and neither descends
from the other, so an `except DeliveryFailed` does not catch a refusal.
`DeliveryFailed` takes `retry_after` when the far side named a delay.

A transport you supply is yours to close. The notifier builds an
`HttpxTransport` when it is given none, and closes that one with itself.

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

`blackboard.wire.NotificationBody.from_json` decodes it, and ignores fields a
later version adds. It refuses a body missing either sequence bound, because
an absent `from_sequence` would decode as zero and send the agent through the
whole level. Any 2xx means the agent took it.
[Write an agent](writing-an-agent.md) covers what the agent does next.
