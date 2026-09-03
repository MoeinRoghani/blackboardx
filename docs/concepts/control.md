# The control component

An agent decides its own work. Every other decision belongs here: who is notified, which writes are admitted, and when the run ends.

How an agent reaches its decision is outside this component and outside the library. An agent that runs an algorithm and an agent that asks a language model reach the control component through the same calls, and it treats their writes alike. [Let a model decide](../guides/deciding-with-a-model.md) covers the agent that asks a language model.

## Admission

A write made through the control component passes the application's rule before the board sequences it.

```python
def rule(proposed, reader):
    if isinstance(proposed, ProposedContribution) and proposed.level == "platform":
        if any(c.content == proposed.content for c in reader.read_level("platform")):
            return Reject("already on the board")
    return Accept()
```

The rule receives a read handle on the board, so it decides from what is already there rather than from the proposed write alone.

The rule is called on every proposed write of both kinds, so a rule that reads a field off the proposal narrows to the kind that carries that field: a `ProposedContribution` carries `level` and `content`, and a `ProposedPremiseWrite` carries `premise`, `value` and `expected_version`. An exception the rule raises comes out of the write call that ran it.

Every write names its writer as a keyword argument.

```python
control.write("platform", {"cause": "a bad deploy"}, writer="ocp")

state = control.reader.read_premise("window")
control.set_premise("window", "20:00-22:00", state.version, writer="ocp")
```

The name reaches the rule on the proposal, and it reaches the audit on the event. No write call checks that the name was registered, so an operator or a scheduled job writes the way an agent does. `Control.as_agent(name)` returns the board as that agent sees it, with the name already bound, and an agent body is written against that view.

Refusals come back as values, because a refusal can race correct agent code. A caller defect raises instead.

| Outcome | Cause |
| --- | --- |
| `Written` | Sequenced, with its number |
| `Written(repeated=True)` | The idempotency key had written this already, and nothing was added |
| `Conflict` | A premise write named a version other than the current one |
| `Rejected(ADMISSION)` | The rule refused, with its reason |
| `Rejected(NOT_PERMITTED)` | The level is outside the `writes_to` a registered agent declared |
| `Rejected(RUN_CLOSED)` | The run has closed |
| raises `UndeclaredRegionError` | No region of that name |
| raises `RegionKindError` | A level operation named a premise, or the reverse |
| raises `IdempotencyKeyError` | The key had written a different region |

`RejectionCause` has these three members and no others. A region that nobody declared raises wherever it is named, from a read as much as from a write, and a key that already wrote another region raises out of the store the control component wrote to. The application declared the regions and the caller chose the key, and no retry changes the regions or the key, so those are not decisions this run made about the write.

The rule runs without the control component's lock, so two writes judged at the same moment are both judged against the board as it was before those writes landed. A premise write closes the window between the judgment and the write with its expected version; a level write does not.

## Notification

A notification says the agent is out of date. The notification carries the sequence range and the regions that changed, and no values. The agent reads the board itself, and that read consumes no capacity.

An agent never receives a notification for a change it wrote.

Every other agent subscribed to the changed region is notified, and nothing ranks them. Choosing which of several agents should run, the decision a blackboard scheduler makes, is not made here and this library offers nowhere to make it. [The control problem](../index.md#the-control-problem-and-where-it-moved) covers why.

## What wakes an agent

An agent declares what wakes it when it registers.

```python
Agent(name="ocp", notify=deliver, subscribes_to=["window", "platform"])
```

Any iterable of names serves, and the declaration keeps a `frozenset` of it, so a generator passed here does not empty itself the first time the control component reads that set.

Omitting `subscribes_to` subscribes the agent to **every premise and to no level**. A premise holds something the work was given, and when a premise changes, work already done may have been aimed at the wrong thing, so the default includes all of them. Another agent's conclusion does not change what you compute from, so the default includes no level.

Naming levels is how a finding puts another agent to work without the finding being misdescribed as a premise.

## Batch windows

Regions of both kinds may carry a batch window. A change to a region becomes due after that region's window, and everything the agent has pending is dispatched as one notification when the earliest due instant arrives. A change to a region with a short window therefore takes with it what a region with a longer window is still holding.

A batch window is the only damping this library has. A notification carries no values, so ten writes to a level that a subscriber watches say the same thing ten times, and each one wakes that agent again. Where the agent is a language model, that is ten inferences to learn what one notification would have said.

```python
Level("findings", batch_window=timedelta(seconds=5))
Premise("namespace", batch_window=timedelta(seconds=5))
```

Registering an agent is a catch-up on what is already on the board rather than a burst that needs damping, so a level the agent subscribes to is due at once when it already holds a contribution, whatever that level's window. A premise it subscribes to is due after that premise's window, so an agent subscribed to premises alone waits the shortest of those windows for its first notification.

The default is zero for both kinds, so a change reaches every agent at once unless the application asks otherwise. Zero is the right default for a premise, where delaying would leave agents working from a value already known to be wrong.

## Delivery

The control component holds no lock while it invokes the agent's callback. A notification due at once is delivered by the thread that made the change, before the write or the registration returns. A notification that a batch window is holding is delivered by the thread that the clock closes that window on. Deliveries that a callback sets off by writing are drained by the thread already draining them, rather than nesting inside the callback.

A callback may run the whole agent cycle inline, so a test can drive several agents on one thread. A callback that raises is contained: the rest of the batch is delivered and the writer keeps its result.

## The audit

Every event is recorded in the order it occurred: opening premise values, accepted and rejected writes, dispatches, acknowledgments, and the closing state. Events that reached the board carry their sequence number; a rejected write never reached it and carries none. A conflict is not recorded, because it wrote nothing, and a repeated idempotency key is not recorded, because it added nothing.

The audit holds that each event occurred. The contributions stay on the board. The audit and the contributions together reconstruct the run, and only the contributions survive the process, so an audit that has to outlive the run is written out before the run closes. [Running as a service](service.md) covers what is held where.

`AuditEvent` is the union of the six: `PremiseOpened`, `WriteAccepted`,
`WriteRejected`, `NotificationDispatched`, `NotificationAcknowledged` and
`RunClosed`. `read_audit` returns them in the order each occurred.

`NotificationId` names one notification. It is an `int` underneath, and
`Control.ack` takes a `NotificationId` or an `int`.
