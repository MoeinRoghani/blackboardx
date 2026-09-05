# ADR 0018: A board is opened once, and a run over it many times

Date: 2026-09-02

## Status

Superseded by ADR 0026, which makes `create_model` converge on a board the store already holds, so there is one door rather than two. ADR 0024 had already superseded the reason this record gives for a second door. What this record decided about a board outliving one process stands.

## Context

`docs/concepts/service.md` told a reader to scale by putting different boards on different replicas, and said a replica that dies is replaced and the run it held is started again against the record it left.

No API did that. `create_model` declares its regions, so calling it against a board that exists raised `DuplicateRegionError`. A `Control` built directly with no regions read the record correctly and rejected every write, because it keeps its own map of declared regions and nothing filled it. `create_model` with no regions built a run that refused everything.

So a board identifier was usable once. A replica that died retired its board, and the record it left could be read for ever and written to never. The storage page's claim that reopening a file reads the run back was half true: the reads worked.

## Decision

Two entry points, because there are two intents and confusing them is how a typo becomes a silently empty board.

`create_model` opens a board that does not exist. It declares the regions and writes the opening premise values, so a board that already holds a region of the same name is refused by `store.declare` raising `DuplicateRegionError`. It does not check that the board is otherwise empty.

`attach_model` opens a run over a board that does. It declares nothing and takes no opening premises, because the record holds them and the versions they are at. It refuses a board holding no regions, so attaching cannot quietly become a create whose every write is then rejected.

`regions` is still required when attaching, and is checked against the record. A run must say what it expects, so that a service pointed at the wrong identifier, or at a board another application wrote, is told rather than left to fail at the first write. A name or a kind that disagrees is refused naming the region.

### What a reattached run carries over

| Carries over | Does not |
| --- | --- |
| The regions and their kinds | The agent registry |
| Every contribution | The outstanding notifications |
| Premise values and their versions | The audit |
| The sequence counter | Every agent's cursor |
| The idempotency keys | The notification identifiers, which start again at 1 |

The left column is the record, which is durable. The right column is the run, which is not, and which died with the process. That split is the one `docs/limits.md` already states.

An agent registering against a reattached run is therefore woken as one joining a run already under way, covering everything on the board. That is what an agent that also lost its memory of the run needs.

## Consequences

A board identifier outlives the process that created it, which is what routing by incident identifier requires.

`Control` gains an internal adopt path that records region kinds without declaring them, and seeds its sequence from the last entry of the record. Without the seed, every registration notification would cover a range ending at zero.

Nothing is added to `BoardStore`. `read_regions` and `read_board` already supply what attaching needs, so the conformance suite is unchanged and a store written outside this repository works with `attach_model` without knowing it exists.

Two identifiers for one board across two replicas is still the caller's mistake to avoid. Attaching does not lock, and this library still holds one run in one process at a time.
