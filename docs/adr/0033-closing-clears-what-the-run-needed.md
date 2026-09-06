# ADR 0033: Closing a run clears what it needed

Date: 2026-09-06

## Status

Accepted. States what the original design table recorded and the move to one store dropped.

## Context

The design divided the data three ways: the answer, which lives as long as the board; the outcome, which lives as long as the board; and the coordination a run needs while it runs. Against that third row the table said, in as many words, **deleted on close**.

When the two stores became one, that row moved and the deletion did not. The blueprint came to read that the coordination lives "until the run closes, then stands as the record of it", which nobody decided and which is not true: nothing reads it after a run closes.

So a closed board leaves its watermarks behind, and any notification still owed to a run that has ended, for ever and unread. In a deployment adapter that is dead rows accumulating per run.

## Decision

Closing a run removes the coordination it needed.

The outcome and the set of unfinished agents are stamped into the run row by the write that closes it, so they survive. Everything that write consulted is redundant the instant it lands:

| At close | |
| --- | --- |
| The outcome, its reason, and who did not finish | Kept. It is the result. |
| Each agent's two watermarks | Removed. The unfinished set was computed from them and stamped. |
| The agents themselves | Removed. The run they belong to has ended. |
| Notifications still unsent | Removed. Nobody is owed a wake-up to a run that is over. |

A closed board therefore holds its contributions, its regions, its premise values, and one small row saying how it ended.

## Consequences

The deployment adapters stop accumulating rows nobody reads.

Closing stays one write that one caller wins, so this is work that winner does. A caller answered `False` did not close the run and clears nothing.

Nothing changes for the board. There is still no retention policy, no sweep, and no age after which anything is deleted; `store.delete` removes one board and the library never calls it. What is cleared here belongs to the run, not to the record.

An acknowledgment arriving after a close finds no watermark. It answers a run that has ended, so it changes nothing, which is already what it did.
