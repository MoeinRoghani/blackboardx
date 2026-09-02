# Glossary

Every term this project uses, and what it means here. A term is defined once, in this table, and every page, docstring and identifier uses it in that sense.

## The parts

| Term | Meaning |
| --- | --- |
| **Board** | What the agents of one run share. It stores contributions, orders them, and reads none of them. It outlives the run that opened it, and a later run attaches to the record it left. |
| **Store** | Where records are kept. One store holds many boards, and every operation on it names the board it acts on. Any implementation of `BoardStore` is a store. |
| **Control component** | Everything that decides: which agents are notified, whether a write is admitted, and when the run closes. `Control` in the code. |
| **Application** | The system built on this library. It supplies the agents, the content, the region declarations, and the rules. |
| **Agent** | A participant that reads the board, decides whether it has anything to add, writes, and acknowledges. The creator names the agents a run starts with, and one that joins a run already under way registers itself. The library never creates one. |
| **Skeletal** | Nii's term for a blackboard system that carries no domain knowledge, so an application is built on it by adding knowledge and control. |

## The record

| Term | Meaning |
| --- | --- |
| **Region** | A named part of the board holding one kind of information. Every region is a level or a premise. |
| **Level** | A region holding what the agents worked out. One agent's contribution does not supersede another's, so each write adds to what is there and nothing already stored changes. |
| **Premise** | A region holding one current value under a version. It holds something the work is given rather than something it concluded, which is why it is not a level: there is one correct value at a time and a later one replaces the earlier. |
| **Contribution** | One unit written into a level. |
| **Content** | What any write carries. A contribution's content, and the content a `BoardChange` records for a write of either kind. |
| **Value** | A premise's current content. `PremiseState.value`, and the `value` argument of a premise write. |
| **Conclusion** | What a level holds. Something an agent drew from evidence, which stays beside the evidence it rests on rather than replacing it. A premise is what a conclusion is drawn from; the two are the ends of one axis, given against concluded. |
| **Sequence number** | A write's position in the board's total order, and its address. One counter serves every region of one board. There is no separate identifier. |
| **Version** | A premise's revision count. A premise write names the version it expects to replace and fails if the premise has moved past it. |
| **Board identifier** | Which board a call acts on, and which board a row belongs to. The caller supplies it and the library never interprets it. `Model.board_id`, `Control.board_id` and `AgentBoard.board_id` hand it back, and every notification carries it. |

## The write path

| Term | Meaning |
| --- | --- |
| **Writer** | Whoever made a write, named in every write call and in the two audit events a write raises, `WriteAccepted` and `WriteRejected`. Usually an agent, and not necessarily one: an operator, a supervising component, or a scheduled job reaches a region the same way, and no write call checks that its writer registered. `agent` is used only where the caller must be registered, meaning the recipient of a notification and the caller of `ack`. |
| **Write** | Putting content into a region. A level write adds a contribution; a premise write replaces a value under its version. Both take a sequence number and both report `Written`, whose version is absent on a level write because a level has none. |
| **Admission rule** | The application's function, called on every proposed write before the board sequences anything. It answers `Accept()` or `Reject(reason)`. |
| **Opening value** | The value a premise starts the run with, given by the `premises` argument to `create_model`. It is a write: it reaches the board and takes a sequence number. It bypasses admission, because it is the application's own input rather than a proposal from a writer. `attach_model` takes no opening premises, because the record already holds the values and the versions they are at. |
| **Conflict** | A premise write that named a version other than the current one. It changes nothing and takes no sequence number. |
| **Rejection** | A write the control component refused, with the cause. It never reaches the board and takes no sequence number. The cause is `ADMISSION`, `NOT_PERMITTED`, or `RUN_CLOSED`, and there is no fourth. What the application's own configuration settles, an undeclared region and a reused idempotency key among it, raises instead of being rejected. |

## Notification and closing

| Term | Meaning |
| --- | --- |
| **Notification** | The message telling one agent it is out of date. It carries a sequence range and the regions that changed, and no values. |
| **Batch window** | The interval a change waits before it is due, taken from the region it landed in. Everything an agent has pending is dispatched as one notification when the earliest due instant arrives, so a change to a region with a short window carries with it what a longer one was still holding. Either kind carries a window, and the default of zero dispatches inline. |
| **Acknowledgment** | An agent reporting that it has stopped working on one notification. It says nothing about what the agent found, and it covers every notification outstanding for that agent whose range ends at or before the acknowledged one's, so an agent that answers only the last one it was sent leaves nothing outstanding. |
| **Cursor** | An agent's last acknowledged sequence number. |
| **Subscription** | Which regions wake an agent. Omitting `subscribes_to` subscribes it to every premise and to no level; naming regions subscribes it to exactly those, of either kind. |
| **Run** | One model, from creation to close. |
| **Attach** | Opening a run over a board the store already holds. `attach_model` declares no region and takes no opening premises, and refuses a board holding none. `create_model` declares its regions, so it refuses a board that already holds a region of the same name. The record carries over and the run does not, so the registry, the outstanding notifications, the audit, the cursors and the notification identifiers all start again. |
| **Idle limit** | How long nothing may happen before the run closes. Every write, registration and acknowledgment pushes it out. |
| **Wall clock limit** | The longest a run may last, whatever else is true. |
| **Termination predicate** | The application's function, asked when the idle limit passes, answering whether the run may close. |
| **Outcome** | How a run ended: `Settled`, `WallClockExpired`, or `Aborted`. The first two name the agents that did not finish; an aborted run names none, because the caller ended it rather than the run reaching its own end. |
| **Audit** | The control component's record that each event occurred, in order. |

## Storage, and the two halves

| Term | Meaning |
| --- | --- |
| **`BoardStore`** | The protocol a store implements: eight methods, three that write, four that read, and one that removes a board. Every one names a board first. |
| **Wire body** | One request or response that crosses between a blackboard and an agent, in `blackboard.wire`. Both halves import them, so neither can spell a field differently from the other. `to_json` and `from_json` carry one each way, and `from_json` raises `wire.WireError` on a body that leaves out a field the class requires. |
| **Client** | What an agent calls a blackboard with, in `blackboard.agent`. Bound to one board and one agent name. |
| **`AgentBoard`** | One board as one agent sees it: the four reads and the three writes, each without the agent's own name. `BoardClient` satisfies it over HTTP and `Control.as_agent` returns it in process, so an agent body is written once. |
| **Operation** | One thing an agent can ask a blackboard to do, with the method and path that carry it. The seven in `blackboard.wire` are the seven `AgentBoard` has, one for one. |
| **Transport** | How one notification leaves the process, in `blackboard.delivery`. `HttpxTransport` posts it; another implementation sends it somewhere else. |
| **Lane** | One agent's queue and the worker that drains it. `HttpNotifier.to` opens one and returns it, which is what lets agents be reached at the same time. It is the `notify` callable an `Agent` takes, and closing one releases it without closing the notifier or any other lane, which is how a finished run gives back its threads. |
| **Refusal** | A delivery the agent will answer the same way next time, such as a 400. The notifier reports it rather than retrying. Distinct from a **failure**, which is worth another attempt. |
| **Undelivered** | What the notifier hands `on_failure` for a notification it gave up on: the address, the agent, the notification, how many times the transport was called, and the error that stopped it. |
| **Idempotency key** | A name the caller gives one write so a store writes it once. A key already written answers with what that write produced rather than producing another. A key that already wrote one region and is sent naming another raises `IdempotencyKeyError`. |
| **Repeat** | A write whose key the store had already written. `Written.repeated` says so, and nothing was added. |
| **Delete** | Removing one board's regions, record, premise values, and counter from a store. The application calls it; nothing in the library does. |
| **Schema stamp** | The number a store writes on a record saying which schema wrote it. A record stamped ahead of the library is refused with `SchemaVersionError`, when `SqliteStore` opens the file and before `PostgresStore` or `MongoStore` runs its first operation. |
| **Adapter** | A store backed by a database the application already runs, constructed over a connection the application owns. `PostgresStore` and `MongoStore`. |
| **Conformance suite** | The cases in `blackboard.conformance` that every store implementation must pass. It ships with the package, so a store written outside this repository is held to the same ones. |
| **Record** | What the board holds. Durable where the store is a database. |

## Words this project does not use

**A register.** The region that holds a premise is a `Premise`. "Register" belongs to computer architecture, and the specification that coined it cited nothing for it, while citing Nii for *solution space*, *partial solution* and *skeletal*. The word survives in this package in exactly one place, `register_agent`, where it is a verb and nothing else. A region is *declared*; an agent *registers*.

**A seed.** A premise has an opening value, given by the `premises` argument. "Seed" belongs to random number generation, to databases and to distributed systems, and it named the same content the word *value* already named.

**A wake**, as a noun. What an agent receives is a notification, in the code and in the prose. The verb stays, because it names the effect rather than the thing: a premise change wakes an agent, and what the agent then holds is a notification.

**A budget.** A run has two limits, both durations, and nothing countable is consumed. `RunLimits` carries them.

## One premise, three words

A premise, its content, and the content it began with are three different things. None of the three is another name for either of the others.

| The thing | Its name | Where you meet it |
| --- | --- | --- |
| The region, and what it holds | a **premise** | `Premise("window")` |
| Its content at this moment | the **value** | `read_premise("window").value` |
| The value it starts the run with | its **opening value** | `create_model(premises={"window": ...})` |

A premise's value is what that premise says right now. Its opening value is what it said when the run began.

What a premise holds is never called a fact. Premise and conclusion are a pair on one axis, given against concluded, and "fact" names neither end of it: a conclusion an agent reaches is a fact too, so the word does not separate the two kinds of region.

## One distinction this project keeps

**Complete, and settled.** The termination predicate answers `COMPLETE` about the result: the application has what it needs. `Settled` describes the run: nothing happened for the idle limit. A run settles while the predicate has never been asked, and a predicate answers complete on a run that then expires on the wall clock. They are answers to different questions and neither implies the other.
