# blackboardx

## The problem this architecture was built for

In the early 1970s DARPA funded a programme in speech understanding, and one of the systems built under it was HEARSAY-II, at Carnegie Mellon. What made the task hard was not noise in the recording. It was the shape of the evidence. A stretch of recorded speech admits several readings at once, and the knowledge that settles which reading is right arrives in kinds that have little to do with each other: how the sound behaves, which words exist, which sequences of words are grammatical, which meanings the surrounding conversation makes plausible. Any of those kinds might be the one that resolves a given stretch, and which one it will be is not known until that stretch has been examined.

That rules out writing the system as procedures that call one another. A call fixes what runs next. Fixing what runs next requires knowing which kind of knowledge the next step needs, and that is precisely what nobody knows in advance.

HEARSAY-II therefore gave its specialists a structure to work on rather than a way to reach each other. Each specialist watches that structure, reads whatever on it bears on its own expertise, and writes back what it can conclude. No specialist invokes another, and none of them needs to know which others exist. What puts a specialist to work is the state of the structure.

That structure is the blackboard. It holds partial answers, meaning pieces that settle nothing on their own and that combine into a result, and it holds them where every specialist can see them.

## How the arrangement outlived the problem

Later systems kept the arrangement and replaced the knowledge. HASP, at Stanford, interpreted sonar rather than speech, and its specialists knew about ocean acoustics rather than about phonemes. The arrangement carried over unchanged, which showed that what HEARSAY-II had contributed was separable from what it knew about speech.

H. Penny Nii set that separation out in a survey of blackboard systems published in AI Magazine in 1986. Some systems carry the knowledge of one problem inside them, as HEARSAY-II carried speech and HASP carried sonar. Others supply the machinery alone and leave the knowledge to whoever builds on them. Nii named the second kind *skeletal*: a system that holds "the essential system components from which application systems can be built by the addition of knowledge and the specification of control". AGE, BB1 and GBB were built that way and distributed to be built on. The term marks the absence of domain knowledge rather than the absence of working parts.

## What this library is

`blackboardx` is skeletal in that sense. It supplies the board, which stores what the agents write and puts every write in one order, and the control component, which determines which agents are told of a change, whether a proposed write is admitted, and when the run ends.

Everything belonging to a particular problem stays with the application: the agents, the content they write, the regions the board holds, and the rules the control component applies. The library never interprets a contribution, because interpreting one takes the expertise of the agent that produced it.

The record outlives the run that wrote it, so a run is opened in one of two ways. `create_model` opens a board the store does not hold yet, and gives every declared premise its opening value. `attach_model` opens a run over a board the store already holds, and continues the sequence from where the record ends.

It also carries both halves of the conversation between a blackboard and agents deployed as their own services, because that conversation belongs to the arrangement rather than to any one problem.

| Module | What it is for |
| --- | --- |
| `blackboard` | The board, the control component, the two ways to open a run, and the four stores |
| `blackboard.wire` | The bodies and operations both halves speak |
| `blackboard.server` | Answering an agent's request, without a web framework |
| `blackboard.delivery` | Sending a notification to an agent, without the writer waiting |
| `blackboard.agent` | Reading and writing a board from an agent |
| `blackboard.conformance` | The suite a store of your own is held to |

An agent reads and writes through `AgentBoard`, which is one board as one agent sees it: the four reads and the three writes, each without the agent's own name. `Control.as_agent` returns one for an agent running in the same process as the run, and `BoardClient` is one for an agent reaching the board over HTTP, so an agent body is written once and deployed either way.

Your service keeps its own HTTP server, its routes, its authentication, and its database. Neither half writes the protocol between them.

## What the arrangement leaves open

Agents that share a record still need four questions answered before any of them can run.

| Question | The answer here |
| --- | --- |
| What causes an agent to run, when nothing assigns it work | A change to a region it subscribes to |
| How an agent learns what the others found, when no agent calls another | It reads the board |
| What the record holds when two agents write to one place at once | A level keeps both in order; a premise keeps one, guarded by a version |
| How the group establishes that nothing further is coming | The run closes on silence, or on time |

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
| Know what it will not do | [What this version does not do](limits.md) |
