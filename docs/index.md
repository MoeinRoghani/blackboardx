# blackboardx

## The problem this architecture was built for

In the early 1970s DARPA funded a programme in speech understanding, and one of the systems built under it was HEARSAY-II, at Carnegie Mellon. What made the task hard was not noise in the recording. It was the shape of the evidence. A stretch of recorded speech admits several readings at once, and the knowledge that settles which reading is right arrives in kinds that have little to do with each other: how the sound behaves, which words exist, which sequences of words are grammatical, which meanings the surrounding conversation makes plausible. Any of those kinds might be the one that resolves a given stretch, and which one it will be is not known until that stretch has been examined.

That unpredictability rules out writing the system as procedures that call one another. A call fixes what runs next. Fixing what runs next requires knowing which kind of knowledge the next step needs, and that is precisely what nobody knows in advance.

HEARSAY-II therefore gave its specialists a structure to work on rather than a way to reach each other. Each specialist watches that structure, reads from it whatever bears on its own expertise, and writes back what it can conclude. No specialist invokes another, and none of them needs to know which others exist. What puts a specialist to work is the state of the structure.

That structure is the blackboard. It holds partial answers, meaning pieces that settle nothing on their own and that combine into a result, and it holds them where every specialist can see them.

Removing the calls left a decision behind. Several specialists can have something to add at the same moment, and HEARSAY-II ran them one at a time. A specialist declared in advance the kinds of change it cared about, so a change triggered only the specialists that had asked for it. Its scheduler ranked what that left, on an estimate of what each would contribute, and ran the highest.

## How the arrangement outlived the problem

Later systems kept the arrangement and replaced the knowledge. HASP, at Stanford, interpreted sonar rather than speech, and its specialists knew about ocean acoustics rather than about phonemes. The arrangement carried over unchanged, which showed that what HEARSAY-II had contributed was separable from what it knew about speech.

H. Penny Nii set that separation out in a survey of blackboard systems published in AI Magazine in 1986. Some systems carry the knowledge of one problem inside them, as HEARSAY-II carried speech and HASP carried sonar. Others supply the machinery alone and leave the knowledge to whoever builds on them. Nii named the second kind *skeletal*: a system that holds "the essential system components from which application systems can be built by the addition of knowledge and the specification of control". AGE, BB1 and GBB were built that way and distributed to be built on. The term marks the absence of domain knowledge rather than the absence of working parts.

## What this library is

`blackboardx` is skeletal in that sense. It supplies the board, which stores what the agents write and puts every write in one order, and the control component, which determines which agents are told of a change, which proposed writes are admitted, and when the run ends.

Everything belonging to a particular problem stays with the application: the agents, the content they write, the regions the board holds, and the rules the control component applies. The library never interprets a contribution, because interpreting one takes the expertise of the agent that produced it.

That expertise is what Nii calls the knowledge a skeletal system is built on, and an application supplies it in whichever form it holds it. An agent whose expertise is an algorithm decides in its own code. An agent whose expertise is a language model puts the decision to the model, offering it the board as tools through `blackboard.tools`, and makes each call the model asks for. The board and the control component behave the same way under both, because the board sequences a contribution and the control component applies the admission rule without reading what the contribution says.

The record outlives the run that wrote it, so a run is opened in one of two ways. `create_model` opens a board the store does not hold yet, and gives every declared premise its opening value. `attach_model` opens a run over a board the store already holds, and continues the sequence from where the record ends.

The library also carries both halves of the conversation between a blackboard and agents deployed as their own services, because that conversation belongs to the arrangement rather than to any one problem.

| Module | What it is for |
| --- | --- |
| `blackboard` | The board, the control component, the two ways to open a run, and the four stores |
| `blackboard.wire` | The bodies and operations that both halves speak |
| `blackboard.server` | Answering an agent's request, without a web framework |
| `blackboard.delivery` | Sending a notification to an agent, without the writer waiting |
| `blackboard.agent` | Reading and writing a board from an agent |
| `blackboard.tools` | Each method of `AgentBoard` as a tool a language model can call, for the application to choose among |
| `blackboard.conformance` | The suite a store of your own is held to |

An agent reads and writes through `AgentBoard`, which is one board as one agent sees it: the four reads and the three writes, each without the agent's own name. `Control.as_agent` returns an `AgentBoard` for an agent running in the same process as the run, and `BoardClient` is one for an agent reaching the board over HTTP, so an agent body is written once and deployed either way.

Your service keeps its own HTTP server, its routes, its authentication, and its database. The library supplies the protocol between them.

## What the arrangement leaves open

Agents that share a record still need four questions answered before any of them can run.

| Question | The answer here |
| --- | --- |
| What causes an agent to run, when nothing assigns it work | A change to a region it subscribes to |
| How an agent learns what the others found, when no agent calls another | It reads the board |
| What the record holds when two agents write to one place at once | A level keeps both in order; a premise keeps one, guarded by a version |
| How the group establishes that nothing further is coming | The run closes on silence, or on the wall-clock limit |

## The control problem, and where it moved

The control problem is choosing which of the specialists that could run is the one to run next, and it outlived HEARSAY-II. Some of the systems after it kept asking how such a choice should be made. Barbara Hayes-Roth answered in 1985 by making control a blackboard problem of its own: a second blackboard, holding the system's reasoning about what to do next, with specialists of its own working on it. BB1 was the system built that way.

The distributed answer kept the blackboard and moved the choice into the participants. Victor Lesser and Daniel Corkill's Distributed Vehicle Monitoring Testbed, in 1983, put a blackboard system in each node of a network, each node scheduling its own work, and was built to study how nodes so arranged reach a coherent result. The cooperative distributed problem solving work of the same years put the coordination among the participants rather than above them. Linda, David Gelernter's language for coordinating parallel processes, reached the same position from another direction. Its processes do not call one another: they put tuples into a shared tuple space and take out the ones matching a template they name, and the tuple space ranks no process above another.

One thing that separated the two answers was where the cost of finding out what a specialist had to say could be paid. HEARSAY-II could pay part of it apart from the work: the declaration each specialist made in advance could be checked without running anything, and a signal that separable is what a central ranking needs. An agent carrying a language model cannot separate the two, because finding out what it has to contribute is the same reading and reasoning as making the contribution. No separable estimate is left for a ranking to act on.

`blackboardx` takes the distributed answer further than the Testbed did. Each node of the Testbed ran a scheduler of its own, and no part of this library runs one. Its control component notifies and does not rank: every agent subscribed to the region a write changed is notified, apart from the agent that made the write, and there is no priority anywhere in an agent's declaration.

## Where to start

| If you want to | Read |
| --- | --- |
| Run something in a minute | [Quickstart](quickstart.md) |
| Understand what the parts mean | [The board](concepts/board.md) |
| Do a particular thing | [Write an agent](guides/writing-an-agent.md) |
| Look a name up | [API reference](reference.md) |
| Check what a term means | [Glossary](glossary.md) |
| Choose where the record is kept | [Storage](concepts/storage.md) |
| Deploy it behind a service | [Running as a service](concepts/service.md) |
| Serve agents that run elsewhere | [Serve a blackboard over HTTP](guides/serving-a-blackboard.md) |
| Let a language model decide what an agent writes | [Let a model decide](guides/deciding-with-a-model.md) |
| Know what it will not do | [What this version does not do](limits.md) |
