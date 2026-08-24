# ADR 0007: Agents register themselves

Date: 2026-08-21

## Status

Accepted. Supersedes the specification's second creation input, agent declarations, and its rule that an agent registered during a run starts with its cursor at the current sequence number.

## Context

The specification creates a model from six things, of which the second is the agent declarations, and it registers those agents when the run opens so that the seed wakes all of them at once. That holds when the application and its agents are one process.

Agents in this deployment are separate services that start on their own schedule. A blackboard is created before they exist, so a creator naming them would be naming deployments it cannot see, matching on names that drift. Nothing to name is present at the moment of creation.

The specification also has an agent registered during a run start with its cursor at the current sequence, receiving notification of subsequent changes only. Under that rule every agent, since every agent now registers after creation, would miss the seed and wait for a premise to move before doing anything.

## Decision

`create_model` takes five things: region declarations, seed register values, an admission rule, a termination predicate, and run budgets. It names no agents.

`register_agent` is the only path by which an agent comes to exist, and it carries that agent's callback, which a creator could not supply.

Registering issues one notification to the agent that registered, covering the registers that currently hold a value, and its cursor starts at zero. A newly registered agent is out of date with the whole board, and the wake says so.

Registering never completes a run. A run into which no agent has ever registered has not begun, so a quiet moment before the first registration is the gap before the work rather than the end of it.

## Consequences

- A creator needs no knowledge of which agents exist, and an agent needs no entry in anyone's configuration.
- The seed wakes nobody, because nobody is registered when it is written. The first wake any agent receives is its own registration.
- A blackboard with no agents stays open until a budget ends it, which is correct: nobody joined.
- An application that did know its agents in advance now registers them in a loop after creation rather than passing a list.

## Alternatives rejected

- **Naming agents at creation.** It requires the creator to know deployment names, and in a deployment where agents start after the blackboard exists there is nothing to name.
- **A catalogue the service consults at creation.** It reintroduces the same coupling one level away, and an agent that has not started yet is absent from the catalogue too.
- **Leaving the cursor at the current sequence.** Every agent would miss the seed, and a blackboard whose premises never change afterwards would produce nothing at all.
