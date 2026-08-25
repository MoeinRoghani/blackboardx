# ADR 0008: A level write can notify

Date: 2026-08-21

## Status

Accepted. Supersedes the specification's rule that registers are the only regions that notify and that a level write reaches no one.

## Context

The specification has registers notify every agent and levels notify nobody. Its reason is that an agent takes its input from the registers, so a level write carries nothing that would change what any agent does.

That holds when every agent computes from the premises alone. It does not hold when one agent's conclusion is another agent's input, which is the ordinary shape of a pipeline where an agent examining a code repository should act on what an agent examining a cluster just found.

The specification's own path for that case is to promote the finding into a register. Doing so states that a conclusion is a premise, which it is not, and it wakes every agent rather than the ones that care.

The rule was also defended on the grounds that levels notifying nobody makes a run terminate by construction. It does not. An agent may call `set_premise`, which wakes every agent, any of which may set another register, so a cycle was always reachable through the premises.

## Decision

An agent's `subscribes_to` names regions of either kind. A write to a level notifies every agent that named that level, except the agent that wrote it.

Omitting `subscribes_to` subscribes an agent to every register and to no level. A premise bears on any agent's work, so the default includes all of them; another agent's conclusion does not, so the default includes none.

`Notification.registers` becomes `Notification.regions`, because it now names regions of either kind.

Registering wakes an agent for every subscribed region that holds something, which for a level means it holds at least one contribution.

## Consequences

- A finding can put another agent to work without being misdescribed as a premise, and it wakes only the agents that asked for that level.
- Cycles between agents are now reachable through levels as well as registers. Time is what ends a run, and nothing about this changes that.
- An application that wants the old behaviour writes nothing, because the default subscribes to no level.

## Alternatives rejected

- **Keeping levels silent.** It forces a conclusion to be published as a premise before it can reach anyone, and that wakes every agent instead of the interested ones.
- **Subscribing to levels by default.** Every finding would wake every agent, which costs an inference each time and is rarely what an agent wants.
- **A separate field for level subscriptions.** Two lists expressing one idea, which a reader then has to keep in step.
