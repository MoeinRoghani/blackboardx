# The control component

Whether an agent works is decided by that agent. Every other decision belongs here: who is notified, whether a write is admitted, and when the run ends.

## Admission

A write made through the control component passes the application's rule before the board sequences it.

```python
def rule(proposed, reader):
    if any(c.content == proposed.content for c in reader.read_level("platform")):
        return Reject("already on the board")
    return Accept()
```

The rule receives a read handle on the board, so it may read contributions. The reading limit applies to the board, not to a rule the application wrote.

Refusals come back as values, because a refusal can race correct agent code. A caller defect raises instead.

| Outcome | Cause |
| --- | --- |
| `Accepted` | Sequenced, with its number |
| `Rejected(ADMISSION)` | The rule refused, with its reason |
| `Rejected(NOT_PERMITTED)` | The level is outside the agent's `writes_to` |
| `Rejected(UNDECLARED_REGION)` | No region of that name |
| `Rejected(RUN_CLOSED)` | The run has closed |
| `RegionKindError` raised | A level operation named a register, or the reverse |

The rule runs without the control component's lock, so two writes judged at the same moment are both judged against the board as it was before either landed. A register write closes that window with its expected version; a level write does not.

## Notification

A notification says the agent is out of date. It carries the sequence range and the regions that changed, and no values. The agent reads the board itself, which costs nothing.

An agent never receives a notification for a change it wrote.

## What wakes an agent

An agent declares this when it registers.

```python
Agent(name="ocp", notify=deliver, subscribes_to=["window", "platform"])
```

Omitting `subscribes_to` subscribes the agent to **every register and to no level**. A register holds a premise, and a changed premise means work already done may have been aimed at the wrong thing, so the default includes all of them. Another agent's conclusion does not change what you compute from, so the default includes none.

Naming levels is how a finding puts another agent to work, without being misdescribed as a premise.

## Batch windows

A register may carry a batch window. The window opens when the first change enters an agent's pending set and closes after the interval, dispatching one notification covering everything pending.

```python
Register("namespace", batch_window=timedelta(seconds=5))
```

The default is zero, so a premise change reaches every agent at once. Delaying it would leave agents working from a value already known to be wrong.

## Delivery

The control component invokes the agent's callback holding no lock, on the thread that closed the batch window, or on a thread already draining deliveries when they chain.

A callback may run the whole agent cycle inline, which is what makes single-threaded tests of multi-agent scenarios possible. A callback that raises is contained: the rest of the batch is delivered and the writer keeps its result.

## The audit

Every event is recorded in the order it occurred: seed writes, accepted and rejected writes, dispatches, acknowledgments, and the closing state. Events that reached the board carry their sequence number; a rejected write never reached it and carries none.

The audit holds that each event occurred. The contributions stay on the board. The two together reconstruct the run.
