# ADR 0012: A creator names the agents, and one may still join later

Date: 2026-08-25

## Status

Accepted. Supersedes ADR 0007 on who names the agents. The cursor rule that ADR 0007 established is kept.

## Context

ADR 0007 removed agent declarations from `create_model`, on the grounds that a blackboard is created before its agents exist and a creator would be naming deployments it cannot see. It made `register_agent` the only way an agent comes to exist.

It never said how an agent learns that a run exists. Registration was treated as something an agent simply does.

An agent learns of a run in one of three ways. Something tells it, it polls for open runs, or it subscribed earlier and is enrolled. Each of those requires a roster held somewhere at the moment the run is created: the caller holds it, a service holds it, or the agent holds an address for a service that enumerates runs. There is no arrangement in which an agent registers and nothing knew of that agent beforehand.

So the premise that nothing is present to name is false whenever the system works at all. ADR 0007 also rejected a catalogue because an agent that has not started is absent from it, which is no objection: an agent that has not started cannot register either.

The gap had a cost beyond the argument. `Control` arms the wall clock as the last act of construction, so a run created and then left waiting for agents to discover it spends its budget on discovery. A run could expire having done nothing, naming nobody as unfinished because nobody had joined.

## Decision

`create_model` takes `agents`, the agents the run starts with. Each is registered after the premises hold their opening values, so each receives one notification covering everything already on the board.

`register_agent` remains, for an agent that joins a run already under way. Naming the roster at creation is the normal path, and registering afterwards is the exception, so the documentation says which is which.

Nothing about registration itself changes. A registering agent still starts with its cursor at zero and is still told about everything already on the board.

## Consequences

- A run is ready to work the moment it is created, so the wall clock starting at construction is correct rather than merely tolerated.
- Discovery leaves the library entirely. A caller that knows its agents passes them. A service that holds long-lived subscriptions passes what it holds. Neither needs the library's help.
- An application that genuinely cannot name its agents at creation registers them afterwards, exactly as before.
- Two paths lead to a registered agent, and the documentation has to name the normal one rather than presenting both.

## Alternatives rejected

- **Removing `register_agent`.** A specialist brought into an investigation already under way could not join, and the run would have to be started again.
- **A readiness gate, so the clocks start when a quorum has registered.** It solves the budget problem and leaves the discovery problem untouched, which is the one that produced it.
- **Keeping both paths as equals.** The question of how an agent joins a run would still have no single answer, which is the state that hid this gap.
