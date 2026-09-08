# The control component

An agent decides its own work. Every other decision belongs here.

| Decision | Section |
| --- | --- |
| Whether a proposed write reaches the board | [Admission](#admission) |
| Which agents hear that it did, and how soon | [Notification](#notification), [What wakes an agent](#what-wakes-an-agent), [Batch windows](#batch-windows) |
| How a notification reaches an agent | [Delivery](#delivery) |
| When the run ends | [The run](run.md) |

It sits between the agents and [the board](board.md): a write goes through it,
and a read does not.

How an agent reaches its decision is outside this component and outside the library. An agent that runs an algorithm and an agent that asks a language model reach the control component through the same calls, and it treats their writes alike. [Give an agent a language model](../guides/deciding-with-a-model.md) covers the agent that asks a language model.

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

The name reaches the rule on the proposal, and it reaches the record on the contribution. No write call checks that the name was registered, so an operator or a scheduled job writes the way an agent does. `Control.as_agent(name)` returns the board as that agent sees it, with the name already bound, and an agent body is written against that view.

Refusals come back as values, because a refusal can race correct agent code. A caller defect raises instead.

| Outcome | Cause |
| --- | --- |
| `Written` | Sequenced, with its number |
| `Written(repeated=True)` | The idempotency key had written this already, and nothing was added |
| `Conflict` | A premise write named a version other than the current one |
| `Rejected(ADMISSION)` | The rule refused, with its reason |
| `Rejected(NOT_PERMITTED)` | The level is outside the `writes_to` the run records for that agent |
| `Rejected(RUN_CLOSED)` | The run has closed |
| raises `UndeclaredRegionError` | No region of that name |
| raises `RegionKindError` | A level operation named a premise, or the reverse |
| raises `IdempotencyKeyError` | The key had written a different region |

`RejectionCause` has these three members and no others. A region that nobody declared raises wherever it is named, from a read as much as from a write, and a key that already wrote another region raises out of the store the control component wrote to. The application declared the regions and the caller chose the key, and no retry changes the regions or the key, so those are not decisions this run made about the write.

The rule runs without the control component's lock, so two writes judged at the same moment are both judged against the board as it was before those writes landed. A premise write closes the window between the judgment and the write with its expected version; a level write does not.

## Notification

A notification says the agent is out of date. The notification carries the sequence range and the regions that changed, and no values. The agent reads the board itself, and that read consumes no capacity.

An agent never receives a notification for a change it wrote.

Every other agent subscribed to the changed region is notified, and nothing ranks them. Choosing which of several agents should run, the decision a blackboard scheduler makes, is not made here, and there is no priority anywhere in an agent's declaration. [The control problem](../index.md#the-control-problem-and-where-it-moved) covers why.

## What wakes an agent

An agent declares what wakes it when it joins, through either door.

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

A declaration names one of two ways to reach the agent, or both.

| Declared with | Reached by |
| --- | --- |
| `notify` | That callable, in this process |
| `address` | The transport this process was given as `reach`, aimed at the address the run records |
| Both | The callable, which is the faster path where the process has one |

Either way the notification goes on the same paths: when the agent joins, when a write it subscribes to lands, and when a batch window closes. The replica taking a write reaches every agent the run says should hear of it and it can reach, which is not only the ones it was given callables for. A process given neither a callable nor a transport reaches the agent not at all, and leaves what the write recorded for a process that can. That is the mechanism behind [any replica serving any board](service.md).

The one thing a process cannot do for an agent it holds no callable for is batch. A window is held in a pending set, and there is none here, so a write to a region carrying a window records the intent and leaves the sending to [the relay](../guides/ending-a-run.md#closing-a-run-no-process-is-watching). Zero, the default, is unaffected.

The control component holds no lock while it invokes the agent's callback. A notification due at once is delivered by the thread that made the change, before the write or the registration returns. A notification that a batch window is holding is delivered by the thread that the clock closes that window on. Deliveries that a callback sets off by writing are drained by the thread already draining them, rather than nesting inside the callback.

A callback may run the whole agent cycle inline, so a test can drive several agents on one thread. A callback that raises is contained: the rest of the batch is delivered and the writer keeps its result.

## What is written down, and where

The control component keeps no history of its own. Everything it decided is
readable from [the store](storage.md):

| The question | What answers it |
| --- | --- |
| Who wrote this, and when | The contribution's `writer` and `written_at` |
| Was this write refused, and why | The `Rejected` returned to the caller that made it |
| Which agents are in this run, where each is reached, and what each may write to | `store.read_agents` |
| How far has this agent been told, and has it answered | `store.read_agents` |
| How did the run end, and who did not finish | `store.read_run` |
| What was never delivered | `store.unsent` |

`NotificationId` names one notification. It is the sequence its range ends at,
an `int` underneath, and `Control.ack` takes either.
