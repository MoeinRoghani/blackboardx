# ADR 0032: An agent is part of the run, and is written down

Date: 2026-09-06

## Status

Accepted. Supersedes what ADR 0025 decided about the agent roster being configuration. Everything else ADR 0025 decided stands.

## Context

ADR 0025 recorded that the agent roster is configuration, sits beside the admission rule and the termination predicate, and is handed to every process rather than stored. Two things follow from that record and both are wrong.

**It filed a declaration under the one field of it that is not data.** `Agent` carries a name, what it subscribes to, what it may write to, and a callback. Four of those five are data. The record put all five outside the store because the fifth is a function.

**It made the two doors into a run different kinds of thing.** `create_model(agents=...)` and `register_agent` produce one fact: this agent is part of this run. They differ in when, not in what. ADR 0025 went further and called using the second at run time "the same mistake as running replicas with different configuration", which is not a mistake at all; it is the only way an agent that was not named at creation can join.

The consequence is that a process is forced to remember its agents, and that is the only reason any process holds anything. An agent that joined mid-run is reachable by the process that took its request and by nothing else. If that process goes, the agent is unreachable and no other can discover it was ever there, because nothing about it was written down.

## Decision

An agent is part of the run, so it is written where the rest of the run is written.

Both doors write the same row: the name, what it subscribes to, what it may write to, and where to reach it. `create_model` writes the agents it was given, `register_agent` writes one that arrives later, and after either the fact is on the record rather than in a process.

**The line is not configuration against run state. It is whether there is anything to write down.** `regions`, the opening `premises` and `limits` all arrive as arguments to `create_model` and all are written down; the deadlines are what `limits` became. The agents are the one argument that is data and was not. What stays outside the store is what has no data in it at all: the admission rule, the termination predicate, the clock, the closing callback, and the transport that carries a notification to an address.

Where to reach an agent is a string. Reaching it is a function. Only the first is written.

## Consequences

No process holds anything about a run, which is what the rest of the design already assumed. Which process served which request stops mattering, including the request that created the board and the request that registered an agent late.

`Agent.notify` is an opaque callable, so the library never sees an address it could write. Closing this means `Agent` carrying an address as data with the callback as the transport over it, which is a change to the public surface.

An agent belongs to the run and not to the board, so it goes when the run closes, along with the watermarks and any notification still owed. ADR 0033 records that.

The application still owns how it comes by a roster. Hardcoded, pulled from a vault, read from its own table, or carried in the request that triggered the run: this library owns no registry and takes no view. What changes is only that what it is handed is written down instead of remembered.
