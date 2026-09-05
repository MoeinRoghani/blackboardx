# What this version does not do

Every limit here is a limit of the library as it stands, checked against the
code rather than remembered. Where something is designed and not built, this
page says so and points at the design.

## An agent is woken by the process it registered with

The record is durable and so is the run. What is not is the callback.

| | What, and where it lives |
| --- | --- |
| **Run state** | Regions, contributions, premise values and versions, the sequence, idempotency keys, the run's two deadlines and its outcome, and how far each agent has been notified and has answered. All of it is in the store you chose, held in memory by `InMemoryStore` and in the database by `PostgresStore`, through one code path either way. None of it is split between the two. |
| **Configuration** | The regions, the agent roster, the admission rule, the termination predicate, the limits and the clock. The application hands these to every process, and no store holds any of them. The audit belongs here too, and is deprecated. |
| **In flight** | Notifications `HttpNotifier` has queued but not yet sent. |

A write is served by any process. It lands on the record, pushes the idle
deadline, and records how far the agents that should hear of it have been
told. A process that never registered an agent still knows that agent is
owed an answer, and `close_expired` names it when the run closes.

What that process cannot do is reach the agent. A callback is a Python
object, so the process an agent registered with is the only one that can call
it. That process finds the work by reading the record, through
`Control.notify_due`, which delivers what its own agents are owed and names
them. Call it on whatever schedule suits the deployment, beside
`close_expired`. A write taken in the same process notifies inline and needs
no poll, so a run inside one process never calls it.

The delay between a write on one replica and the agent hearing of it is
therefore the poll interval plus whatever is left of the region's batch
window. Choose the interval and the idle limit together: a run whose idle
limit is shorter than the poll interval can settle before the poll notices.

Two replicas holding the same agent name both hold a callback, and both
deliver. A notification carries no values, so a repeat costs the wire
nothing, but it costs an application whose callback does real work. Register
one name in one place.

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

## The audit is deprecated, and unbounded until it goes

`Control.read_audit` returns every event of the run, with no bound and no
cursor. A long run holds every event in the process and hands back all of them
at once, and nothing writes the audit to the store, so it dies with the
process.

It is deprecated and may be removed on or after 2026-12-05. What it recorded
is answered two other ways. A contribution carries its writer and the instant
it was written, so the record says who wrote what and when. Everything else it
held is written to the log.

## The library logs only what a caller cannot see

The rule is one line: an agent knows what it wrote, what it was refused, what
it was notified of and what it acknowledged, because each of those reached it,
so this library says none of them again. It logs a run closing with its
outcome and the agents that did not finish, and a notification that never
arrived after its attempts, because no agent can log what it never received.

It also logs what only the store layer sees: a record written for a schema
this version cannot read, a record stamped forward to this one, and a store
that failed under `close_expired`. A sweep has no caller waiting on it, so a
failure there reaches nobody unless the library says so. One board failing
does not end the pass; the sweep logs it and closes the rest.

Every line goes to the `blackboard` logger through the standard library, and
the application configures the handlers and the format.

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

## A tool schema does not name the board's regions

`blackboard.tools` renders one set of schemas, and they are the same whatever
board they are used against. `blackboard_write` takes a level as a string, and
nothing in that schema says which levels the board holds, so a model learns
them by calling `blackboard_read_regions` or by being told in the prompt it was
given.

A model that names a region the board does not hold is answered with the ones
it does, so the correction costs a turn rather than ending the run.

## The library calls no model API

`blackboard.tools` renders schemas and runs the calls a model asked for. It
sends nothing to a model, holds no conversation, and depends on no provider's
package. The loop, the prompt, and the choice of model are the application's,
and [Let a model decide](guides/deciding-with-a-model.md) shows the loop that
runs around this module.

## The conformance suite defines behaviour, not performance

`blackboard.conformance` decides a store's correctness. It says nothing about
how fast a store is, how it behaves under load, or how many boards it will
hold. Those are yours to measure against your database.

## What is designed and not built

The transactional outbox. A contribution and the intent to notify are not yet
written in one transaction, so a process that commits a write and stops
before delivering loses the notification, which is the limit the section on a
queued notification describes. The design is the standard one: the intent is
a row written with the contribution, the writing process sends immediately
after commit, and a relay picks up whatever the fast path never completed.


