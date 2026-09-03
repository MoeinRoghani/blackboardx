# What this version does not do

Every limit here is a limit of the library as it stands, checked against the
code rather than remembered. Where something is designed and not built, this
page says so and points at the design.

## A run lives in one process

The board is durable and the run is not.

| Held where | What |
| --- | --- |
| The database, through the store | Regions, contributions, premise values and versions, the sequence, idempotency keys |
| The process, in `Control` | The registered agents, outstanding notifications, the audit, the idle and wall clock timers |
| The process, in `HttpNotifier` | Notifications queued but not yet sent |

A second replica holding its own `Control` for the same board knows no agent
that the first registered, owes no notification that the first dispatched, and
measures
silence from its own start. Losing the replica that holds a run ends that run:
the record survives and the run does not resume.

So one board is served by one `Control` in one process at a time for
**writing**. Scale by putting different boards on different replicas and
routing writes by board identifier, not by putting more replicas behind one
board.

Reads are exempt. Give `BoardService` the store and any replica answers a read
for any board in it, with a run open or without one, because a read needs the
record
rather than the run. The audit is the one read that is not exempt: it lives
in the process and no operation on the wire exposes it.

A replacement replica opens a run over the record with `attach_model`, which
carries the record and not the run: the registry, the outstanding
notifications, the audit, the cursors and the notification identifiers all
start again. Work that an agent had finished but not acknowledged is done twice
unless the agent's writes carry idempotency keys.

[Running as a service](concepts/service.md) covers the deployment that follows
from this. A run that resumes rather than restarts is designed and not built.

## A queued notification does not survive a restart

`HttpNotifier` holds its queue in memory. A process that stops loses whatever
had not been sent. `close` waits up to `close_timeout` in total, then
abandons what is left and reports each one through `on_failure` before it
returns.

That loss usually costs nothing, because a notification carries no values and
the
next one covers the range a lost one would have covered. It costs something
when the lost one is the last, and the run then waits until its idle limit
closes it.

## The audit is unbounded and in memory

`Control.read_audit` returns every event of the run, with no bound and no
cursor. A long run holds every event in the process and hands back all of them
at once. Nothing writes the audit to the store.

## There is no authentication and no authorisation

`BoardService` authenticates nobody. It does not check that the `writer` named
in a body is the caller who sent it, and it has no notion of which agents may
write where beyond the `writes_to` an application declared.

Each operation has its own path and method so that a gateway in front of the
service can carry those policies, which is the deployment this library is
built for. Check the caller in your route, before calling into the service.

## Content must be JSON

Content crosses every store as JSON, including the in-memory one, so what is
written comes back as what JSON carries: a tuple comes back as a list, and
content that JSON cannot carry raises `TypeError` before anything is stored.

That is deliberate rather than pending. A store that held Python objects as
they stand would accept in a test what a deployment then refuses.

## An idempotency key is not compared against the write it names

A key already written returns the first write's outcome whatever content
arrives with it. Only the region is compared, and a key that already wrote one
region and is then sent naming another raises `IdempotencyKeyError`, which
over HTTP is a 409 whose body reads `{"error": "idempotency_key_reused"}`.

Comparing bodies means normalising what JSONB and BSON did to them on the way
in, which is where a false mismatch would come from. A retry is expected to
send what it sent before.

## A read over HTTP stops at a thousand

A read in process takes `limit=None`: `model.reader.read_level`
returns every contribution from the sequence bound. A read over HTTP is
bounded twice. It answers with `wire.DEFAULT_LIMIT`, a hundred, when the
caller names no limit, and never with more than `wire.MAX_LIMIT`, a thousand,
whatever the caller asks for.

The cap is silent. A request for five thousand is answered with a thousand and
no error, and `has_more` is the only thing that says the level continues. A
caller that reads a whole level follows `has_more` and moves `from_sequence`
past the last sequence it saw. `BoardClient.read_level` and
`BoardClient.read_board` do that themselves when they are given no `limit`;
given a `limit`, they make a single request and return at most that many
contributions.

## A batch window is not on the record

A store holds a region's name and its kind. It holds no batch window, so
`read_regions` returns every region with the default window of zero, whatever
window was declared, and `wire.RegionBody.declaration` rebuilds it the same way
on the other side.

The window is configuration rather than record, so a run that attaches must
state its windows again in the `regions` it passes. `attach_model` compares
names
and kinds against the record and nothing else, and a window that disagrees
with the previous run's is not reported.

## A record is never stamped backwards

A store refuses a record written for a schema it cannot read, and never
rewrites one to an earlier schema. If it did, an older version of the library
would read fields a newer one wrote and take them at face value.

Upgrade forwards. There is no supported path back.

## The blackboard reaches out; the agent does not

An agent is notified by a request the blackboard makes to an address the agent
gave it, so an agent has to be reachable at an address. There is no long poll,
no stream, and no queue an agent subscribes to. An agent behind a network that
will not accept an inbound request cannot be notified by this library.

## Deleting is the application's to schedule

`store.delete` removes one board. Nothing in the library calls it: a run that
closes deletes no board, and no board expires. There is no retention policy,
no sweep, and no age after which anything is deleted.

`store.delete` also cannot see a live run. Close the run before deleting the
board it is
serving.

## The conformance suite defines behaviour, not performance

`blackboard.conformance` decides a store's correctness. It says nothing about
how fast a store is, how it behaves under load, or how many boards it will
hold. Those are yours to measure against your database.

## What is designed and not built

A run whose state lives in the database rather than in a process, so that a
replacement replica resumes rather than restarts. That means the registered
agents, the outstanding notifications, the audit, and the deadlines moving
into the store alongside the record, and a sweep that closes runs whose limits
have passed.


