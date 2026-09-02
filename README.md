# blackboardx

A skeletal blackboard system for Python.

The blackboard architecture came out of HEARSAY-II, a speech understanding system built at Carnegie Mellon in the early 1970s under a DARPA programme. Its difficulty was that a stretch of speech admits several readings, and the knowledge that settles which one is right arrives in unrelated kinds: acoustic, lexical, syntactic, semantic. Which kind will settle a given stretch is not known until that stretch is examined, so the system could not be written as procedures calling one another, because a call fixes what runs next. HEARSAY-II gave its specialists a shared structure to work on instead. Each reads what bears on its own expertise and writes back what it concludes, and none of them calls another.

Later systems kept that arrangement and replaced the knowledge, HASP interpreting sonar where HEARSAY-II interpreted speech. H. Penny Nii, surveying blackboard systems in AI Magazine in 1986, named a system *skeletal* when it supplies the components alone and leaves the knowledge and the control to whoever builds on it.

`blackboardx` is skeletal in that sense. It supplies the board, which stores what agents write and puts every write in one order, and the control component, which determines who is notified of a change, whether a write is admitted, and when the run ends. An application supplies its regions, their opening premise values, the agents the run starts with, an admission rule, a termination predicate, and limits.

It also carries both halves of the conversation between a blackboard and agents deployed as their own services: the bodies and operations they share, the piece that answers an agent's request, the piece that sends a notification without making the writer wait, and the client an agent calls with. Your service keeps its own HTTP server, its routes, its authentication, and its database; neither half writes the protocol between them.

The distribution name is `blackboardx`; the import name is `blackboard`. The documentation, including the API reference, is at <https://moeinroghani.github.io/blackboardx/>.

## Install

```
pip install blackboardx
pip install 'blackboardx[postgres]'    # PostgresStore
pip install 'blackboardx[mongodb]'     # MongoStore
pip install 'blackboardx[notifier]'    # sending to agents over HTTP
```

The base install has no runtime dependency: the board it ships, `SqliteStore`, is backed by SQLite, which comes with Python. A deployment keeps the record in the database it already runs, and the adapter for one needs its driver.

## Documentation

| | |
| --- | --- |
| [Quickstart](https://moeinroghani.github.io/blackboardx/quickstart/) | A run in full, in twenty lines |
| [Concepts](https://moeinroghani.github.io/blackboardx/concepts/board/) | What the board, the control component and a run are |
| [Storage](https://moeinroghani.github.io/blackboardx/concepts/storage/) | Where the record is kept, and what an adapter owes |
| [Guides](https://moeinroghani.github.io/blackboardx/guides/writing-an-agent/) | Writing an agent, notifying over HTTP, admission rules, ending a run, testing |
| [Serve a blackboard](https://moeinroghani.github.io/blackboardx/guides/serving-a-blackboard/) | Answering agents that run as their own services |
| [What it does not do](https://moeinroghani.github.io/blackboardx/limits/) | Every limit of this version, in one place |
| [API reference](https://moeinroghani.github.io/blackboardx/reference/) | Every exported name |

## Example

```python
from datetime import timedelta

from blackboard import (
    Agent,
    Level,
    Premise,
    RunLimits,
    Settled,
    SqliteStore,
    create_model,
)

notifications = []

model = create_model(
    board_id="incident-4471",
    store=SqliteStore("incidents.sqlite3"),
    regions=[Level("platform"), Premise("window")],
    premises={"window": ["2026-08-16T20:00", "2026-08-16T22:00"]},
    agents=[Agent(name="ocp", notify=notifications.append)],
    limits=RunLimits(wall_clock=timedelta(minutes=10), idle=timedelta(seconds=1)),
)

(notification,) = notifications
window = model.reader.read_premise("window").value
model.control.write("platform", {"window": window, "findings": ["oom"]}, writer="ocp")
model.control.ack(notification.notification_id, agent="ocp")

assert model.control.wait_closed(timeout=timedelta(seconds=10)) == Settled()
```

## License

Apache-2.0. The license text is in [LICENSE](https://github.com/MoeinRoghani/blackboardx/blob/main/LICENSE), and every distribution carries it.
