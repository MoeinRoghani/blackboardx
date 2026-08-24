# blackboardx

A group of agents works on one problem. Each writes what it finds into a single shared record, every agent can read all of it, and no agent calls another; the record is the only channel between them. The blackboard literature calls a system skeletal when it supplies this structure with no domain knowledge inside, so that an application system is built on it by adding knowledge and control. `blackboardx` is skeletal in that sense. It supplies the board and the control component; an application creates a model by supplying its regions, seed, admission rule, termination predicate, and budgets, and its agents register themselves into it.

The distribution name is `blackboardx`; the import name is `blackboard`. The documentation, including the API reference, is at <https://moeinroghani.github.io/blackboardx/>.

## Install

```
pip install blackboardx
```

## The board

The board stores contributions in named regions under one total order, and it never reads what it stores. A region has one of two kinds. A level accumulates contributions in arrival order, and nothing stored is altered. A register holds one current value for a premise of the case; a write replaces the whole value under the version the writer read, and fails with the register's current version when another writer moved it first. One counter orders every write across all regions, so a contribution in one region stands in a definite order against a write in any other.

## Public API

Every public name is exported from `blackboard`; every other module is internal.

| Name | Holds |
| --- | --- |
| `Board` | The board: `declare`, `append`, `set`, `read_level`, `read_register`, `read_board` |
| `Level`, `Register` | The two region declarations |
| `Written`, `Conflict` | A register write the board sequenced, and one that named a stale version |
| `Contribution` | One unit read back from a level |
| `RegisterState` | A register's current value and version |
| `BoardChange` | One write to any region, as `read_board` returns it |
| `BlackboardError` | The base of every error the library raises |
| `UndeclaredRegionError` | An operation named a region that no declaration created |
| `DuplicateRegionError` | A declaration named a region that already exists |
| `RegionKindError` | An operation that takes a level named a register, or the reverse |
| `UnsetRegisterError` | A register was read before any write gave it a value |
| `BoardReader` | The three read operations, as the admission rule receives them |
| `BoardStore` | The operations the control component performs on a board, so an application can supply its own |
| `ProposedContribution`, `ProposedRegisterWrite`, `ProposedWrite` | A write as the admission rule sees it, before sequencing |
| `Accept`, `Reject` | The admission rule's two verdicts |
| `AdmissionRule` | The type of the rule the application supplies |
| `Accepted`, `Rejected` | A write the control component admitted, and one it refused |
| `RejectionCause` | The closed set of causes for a refused write, including a level the agent did not declare |
| `WriteAccepted`, `WriteRejected`, `AuditEvent` | The audit's records of writes that reached the board and writes that did not |
| `Agent` | An agent declaration: name, delivery callback, the regions it subscribes to, and the levels it may write |
| `Notification`, `NotificationId` | One wake, naming the regions that changed, and the identifier an acknowledgment names |
| `NotificationDispatched`, `NotificationAcknowledged` | The audit's records of a wake being dispatched and acknowledged |
| `DuplicateAgentError` | A registration named an agent that is already registered |
| `UnknownNotificationError` | The named notification was never issued to the acknowledging agent |
| `Clock`, `ScheduledCall` | The protocol for reading time and arming calls, and an armed call's handle |
| `TerminationDecision`, `TerminationPredicate` | The predicate's two answers, and the type of the predicate the application supplies |
| `RunBudgets` | The two limits on a run, both durations: the wall clock and the idle limit |
| `Settled`, `WallClockExpired`, `Aborted`, `RunOutcome` | The three states a run closes in, each naming the agents that did not finish |
| `RunClosed` | The audit's record of the run closing |
| `RunClosedError` | A declaration or registration reached a run that has closed |
| `Model`, `create_model` | A running model's read handle and control component, and the one creation path. Agents are not named here |
| `Control` | The control component an application drives: writes, acknowledgment, mid-run declaration and registration, abort, the audit, and the outcome |
| `SeedError` | The seed's names are not exactly the declared registers |
| `RegisterSeeded` | The audit's record of the seed writing one register when the run opened |
| `SystemClock` | The default clock, the library's only reader of the operating system clock |
| `ManualClock` | The deterministic clock a test advances by hand |

## Example

```python
from datetime import timedelta

from blackboard import Agent, Level, Register, RunBudgets, Settled, create_model

wakes = []

model = create_model(
    regions=[Level("platform"), Register("window")],
    seed={"window": ("2026-08-16T20:00", "2026-08-16T22:00")},
    budgets=RunBudgets(wall_clock=timedelta(minutes=10), idle=timedelta(seconds=1)),
)

# An agent registers itself, and registering wakes it.
model.control.register_agent(Agent(name="ocp", notify=wakes.append))

# The agent's cycle: read the premises, contribute, acknowledge.
(wake,) = wakes
window = model.reader.read_register("window").value
model.control.write("ocp", "platform", {"window": window, "findings": ["oom"]})
model.control.ack("ocp", wake.notification_id)

assert model.control.wait_closed(timeout=timedelta(seconds=10)) == Settled()
for contribution in model.reader.read_level("platform"):
    print(contribution.sequence, contribution.content)
```

## License

Apache-2.0. The license text is in [LICENSE](https://github.com/MoeinRoghani/blackboardx/blob/main/LICENSE), and every distribution carries it.
