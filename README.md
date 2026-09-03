# blackboardx

A skeletal blackboard system for Python.

The blackboard architecture came out of HEARSAY-II, a speech understanding system built at Carnegie Mellon in the early 1970s under a DARPA programme. Its difficulty was that a stretch of speech admits several readings, and the knowledge that settles which one is right arrives in unrelated kinds: acoustic, lexical, syntactic, semantic. Which kind will settle a given stretch is not known until that stretch is examined, so the system could not be written as procedures calling one another, because a call fixes what runs next. HEARSAY-II gave its specialists a shared structure to work on instead. Each reads what bears on its own expertise and writes back what it concludes, and none of them calls another.

Later systems kept that arrangement and replaced the knowledge: HASP interpreted sonar rather than speech. H. Penny Nii, surveying blackboard systems in AI Magazine in 1986, named a system *skeletal* when it supplies the components alone and leaves the knowledge and the control to whoever builds on it.

`blackboardx` is skeletal in that sense. It supplies the board, which stores what agents write and puts every write in one order, and the control component, which determines who is notified of a change, which writes are admitted, and when the run ends. An application supplies its regions, their opening premise values, the agents the run starts with, an admission rule, a termination predicate, and limits.

The record outlives the run that wrote it. `create_model` opens a board the store does not hold yet; `attach_model` opens a run over a board the store already holds, and continues the sequence from where the record ends.

The library also carries both halves of the conversation between a blackboard and agents deployed as their own services: the bodies and operations they share, the piece that answers an agent's request, the piece that sends a notification without making the writer wait, and the client an agent calls with. Your service keeps its own HTTP server, its routes, its authentication, and its database; the library supplies the protocol between them.

An agent reads and writes through `AgentBoard`, which is the four reads and the three writes without the agent's own name. `Control.as_agent` returns an `AgentBoard` for an agent in the same process as the run, and `BoardClient` is one over HTTP, so an agent body is written once and deployed either way.

What an agent knows is the application's to supply. An agent whose expertise is an algorithm calls those reads and writes itself. An agent whose expertise is a language model offers the same reads and writes to that model as tools, through `blackboard.tools`, and runs the calls the model asks for. The library sends nothing to a model and depends on no provider's package.

The distribution name is `blackboardx`; the import name is `blackboard`. The documentation, including the API reference, is at <https://moeinroghani.github.io/blackboardx/>.

## Install

```
pip install blackboardx
pip install 'blackboardx[postgres]'     # PostgresStore
pip install 'blackboardx[mongodb]'      # MongoStore
pip install 'blackboardx[notifier]'     # sending notifications to agents over HTTP
pip install 'blackboardx[agent]'        # BoardClient, for an agent calling a blackboard
pip install 'blackboardx[conformance]'  # the suite a store of your own is held to
```

The base install has no runtime dependency. `InMemoryStore` holds the record in the process, and `SqliteStore` uses `sqlite3` from the standard library. A deployment keeps the record in the database it already runs, and the adapter for that database needs its driver.

## Documentation

| | |
| --- | --- |
| [Installation](https://moeinroghani.github.io/blackboardx/install/) | The extras, and what each one gives you |
| [Quickstart](https://moeinroghani.github.io/blackboardx/quickstart/) | A run in full, and what each step means |
| [Concepts](https://moeinroghani.github.io/blackboardx/concepts/board/) | What the board, the control component and a run are |
| [Storage](https://moeinroghani.github.io/blackboardx/concepts/storage/) | Where the record is kept, and what an adapter owes |
| [Guides](https://moeinroghani.github.io/blackboardx/guides/writing-an-agent/) | Writing an agent, notifying over HTTP, admission rules, ending a run, testing |
| [Serve a blackboard](https://moeinroghani.github.io/blackboardx/guides/serving-a-blackboard/) | Answering agents that run as their own services |
| [Let a model decide](https://moeinroghani.github.io/blackboardx/guides/deciding-with-a-model/) | Offering the board to a language model as tools it can call |
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

Running that example a second time raises `DuplicateRegionError`, because the board is already in `incidents.sqlite3`. `attach_model` opens a run over the board.

## License

Apache-2.0. The license text is in [LICENSE](https://github.com/MoeinRoghani/blackboardx/blob/main/LICENSE), and every distribution carries it.
