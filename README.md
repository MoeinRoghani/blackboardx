# blackboardx

A group of agents works on one problem. Each writes what it finds into a single shared record, every agent can read all of it, and no agent calls another; the record is the only channel between them. The blackboard literature calls a system skeletal when it supplies this structure with no domain knowledge inside, so that an application system is built on it by adding knowledge and control. `blackboardx` is skeletal in that sense. It supplies the board and the control component; an application creates a model by supplying its regions, seed, admission rule, termination predicate, and limits, and its agents register themselves into it.

The distribution name is `blackboardx`; the import name is `blackboard`. The documentation, including the API reference, is at <https://moeinroghani.github.io/blackboardx/>.

## Install

```
pip install blackboardx
```

## Documentation

| | |
| --- | --- |
| [Quickstart](https://moeinroghani.github.io/blackboardx/quickstart/) | A run in full, in twenty lines |
| [Concepts](https://moeinroghani.github.io/blackboardx/concepts/board/) | What the board, the control component and a run are |
| [Guides](https://moeinroghani.github.io/blackboardx/guides/writing-an-agent/) | Writing an agent, admission rules, ending a run, testing |
| [API reference](https://moeinroghani.github.io/blackboardx/reference/) | Every exported name |

## Example

```python
from datetime import timedelta

from blackboard import (
    Agent,
    Level,
    Register,
    RunBudgets,
    Settled,
    SqliteBoard,
    create_model,
)

wakes = []

model = create_model(
    regions=[Level("platform"), Register("window")],
    seed={"window": ["2026-08-16T20:00", "2026-08-16T22:00"]},
    budgets=RunBudgets(wall_clock=timedelta(minutes=10), idle=timedelta(seconds=1)),
    board=SqliteBoard("incident.sqlite3"),
)

model.control.register_agent(Agent(name="ocp", notify=wakes.append))

(wake,) = wakes
window = model.reader.read_register("window").value
model.control.write("ocp", "platform", {"window": window, "findings": ["oom"]})
model.control.ack("ocp", wake.notification_id)

assert model.control.wait_closed(timeout=timedelta(seconds=10)) == Settled()
```

## License

Apache-2.0. The license text is in [LICENSE](https://github.com/MoeinRoghani/blackboardx/blob/main/LICENSE), and every distribution carries it.
