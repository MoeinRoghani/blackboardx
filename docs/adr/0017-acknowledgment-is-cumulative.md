# ADR 0017: An acknowledgment covers the ranges it already includes

Date: 2026-09-02

## Status

Accepted. Refines ADR 0010 on what `Settled.unfinished` holds.

## Context

A notification names a range, from the agent's cursor to the board's last sequence. Acknowledging one advances that agent's cursor to the end of the range, and the cursor is cumulative: it only moves forward.

Acknowledgment itself was not cumulative. It removed exactly the notification identifier it named. So the two structures disagreed, and an agent sent three overlapping notifications and answering the newest had the same cursor as one that answered all three, while the run still held two identifiers against it and named it unfinished.

An agent could avoid that by acknowledging every identifier it was sent. One case it cannot avoid: a notification whose delivery raised. The control component suppresses the exception, by design, so that one agent's failure does not reach an unrelated writer. The agent never receives that notification, so it can never acknowledge it by name. It then holds the run open until the idle limit and is named unfinished for work it did.

## Decision

Acknowledging a notification acknowledges every notification outstanding for that agent whose range ends at or before the acknowledged one's.

Comparison is on `to_sequence` rather than on arrival order, so acknowledging an older, narrower range leaves a newer, wider one outstanding. The agent has not answered the newer range, and the run should still wait for it.

## Consequences

`Settled.unfinished` holds an agent only when a range it has genuinely not answered is outstanding. ADR 0010 defined the set as an agent still holding an unacknowledged notification, which is unchanged in words and narrower in fact.

An agent may acknowledge only the last notification it was sent, which is what an agent that reads to the board's end already does. Acknowledging each one separately still works and is still correct.

A notification lost in delivery no longer holds a run open past the agent's next acknowledgment. It still counts as outstanding until then, which is right: nothing has answered that range yet.

`_Outstanding.generation` is removed. It was set to its default and read nowhere.
