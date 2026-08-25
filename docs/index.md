# blackboardx

A group of agents works on one problem. Each writes what it finds into a single shared record, every agent can read all of it, and no agent calls another. The record is the only channel between them.

The blackboard literature calls a system *skeletal* when it supplies that structure carrying no domain knowledge, so that an application is built on it by adding knowledge and control. `blackboardx` is skeletal in that sense: it supplies the board and the control component, and an application supplies its regions, its agents, and its rules.

## What it is for

Four questions arise whenever independent agents work on one problem, and this library answers each.

| Question | Answer |
| --- | --- |
| What causes an agent to run, when nothing assigns it work | A change to a region it subscribes to |
| How an agent learns what the others found, when no agent calls another | It reads the board |
| What the record holds when two agents write to one place at once | A level keeps both in order; a register keeps one, guarded by a version |
| How the group establishes that nothing further is coming | The run closes on silence, or on time |

## Where to start

| If you want to | Read |
| --- | --- |
| Run something in a minute | [Quickstart](quickstart.md) |
| Understand what the parts mean | [The board](concepts/board.md) |
| Do a particular thing | [Write an agent](guides/writing-an-agent.md) |
| Look a name up | [API reference](reference.md) |
| Choose where the record is kept | [Storage](concepts/storage.md) |
| Deploy it behind a service | [Running as a service](concepts/service.md) |
