# ADR 0026: A declaration converges rather than being made once

Date: 2026-09-05

## Status

Accepted. Supersedes ADR 0018.

## Context

ADR 0018 gave the library two entry points. `create_model` declared regions and refused a board that already held one of the same name; `attach_model` declared nothing and refused a board that held none. The reason recorded there was that two intents exist and confusing them is how a typo becomes a silently empty board.

ADR 0024 and ADR 0025 then moved the run into the store, and `attach_model` was deprecated on the grounds that `create_model` had become the one door. It had not. `create_model` still raised `DuplicateRegionError` on a board that existed, so the deprecation named a replacement that could not do the job, and the only working door for a second replica was the one marked for removal.

The deeper problem is that two doors ask a process a question it cannot answer. Replicas are identical and start in any order, so "am I the first" is not a fact about the application, it is a race.

## Decision

One door. `create_model` converges: a region the record already holds with the same name and the same kind is recorded and not written again.

A name held as the other kind still raises `RegionKindError`. That is a disagreement about what the board is, not a repeat of a declaration, and it is the check that catches a replica pointed at the wrong identifier or at a board another application wrote.

An opening premise value is written only where the record holds none. It is what a board starts from rather than what every process asserts on arrival, and the record holds the value and the version it is at.

## Consequences

Every replica runs the same call. `create_model` with the same arguments on three processes opens one board and writes to it three times, which is what a deployment of identical replicas needs.

The `attach_model` deprecation now names a replacement that works, and its message says so.

The typo that ADR 0018 guarded against is unchanged. A wrong board identifier still creates a board rather than joining one, exactly as `create_model` always did on a fresh identifier. Convergence adds no new way to get that wrong; it removes a way to get the correct case wrong.

`Control` reads `read_regions` once at construction, which is one round trip a run in a single process did not previously make.
