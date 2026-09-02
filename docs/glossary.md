# Glossary

Every term this project uses, and what it means here. A term is defined once, in this table, and every page, docstring and identifier uses it in that sense.

## The parts

| Term | Meaning |
| --- | --- |
| **Board** | The shared record of one run. It stores contributions, orders them, and reads none of them. |
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
| **Board identifier** | Which board a call acts on, and which board a row belongs to. The caller supplies it and the library never reads it. `board_id`. |

## The write path

| Term | Meaning |
| --- | --- |
| **Writer** | Whoever made a write, named in every write call and every audit event. Usually an agent, and not necessarily one: an operator, a supervising component, or a scheduled job reaches a region the same way, and no write call checks that its writer registered. `agent` is used only where the caller must be registered, meaning the recipient of a notification and the caller of `ack`. |
| **Write** | Putting content into a region. A level write adds a contribution; a premise write replaces a value under its version. Both take a sequence number and both report `Written`, whose version is absent on a level write because a level has none. |
| **Admission rule** | The application's function, called on every proposed write before the board sequences anything. It answers `Accept()` or `Reject(reason)`. |
| **Opening value** | The value a premise starts the run with, given by the `premises` argument to `create_model`. It is a write: it reaches the board and takes a sequence number. It bypasses admission, because it is the application's own input rather than a proposal from a writer. |
| **Conflict** | A premise write that named a version other than the current one. It changes nothing and takes no sequence number. |
| **Rejection** | A write the control component refused, with the cause. It never reaches the board and takes no sequence number. |

## Notification and closing

| Term | Meaning |
| --- | --- |
| **Notification** | The message telling one agent it is out of date. It carries a sequence range and the regions that changed, and no values. |
| **Batch window** | The interval a premise waits after its first pending change, so several changes in quick succession become one notification. |
| **Acknowledgment** | An agent reporting that it has stopped working on one notification. It says nothing about what the agent found. |
| **Cursor** | An agent's last acknowledged sequence number. |
| **Subscription** | Which regions wake an agent. Omitting `subscribes_to` subscribes it to every premise and to no level; naming regions subscribes it to exactly those, of either kind. |
| **Run** | One model, from creation to close. |
| **Idle limit** | How long nothing may happen before the run closes. Every write, registration and acknowledgment pushes it out. |
| **Wall clock limit** | The longest a run may last, whatever else is true. |
| **Termination predicate** | The application's function, asked when the idle limit passes, answering whether the run may close. |
| **Outcome** | How a run ended: `Settled`, `WallClockExpired`, or `Aborted`. Each names the agents that did not finish. |
| **Audit** | The control component's record that each event occurred, in order. |

## Storage

| Term | Meaning |
| --- | --- |
| **`BoardStore`** | The protocol a store implements: seven methods, three that write and four that read. Every one names a board first. |
| **Wire contract** | The request and response bodies that cross between a blackboard and an agent, in `blackboard.wire`. Both halves import them, so neither can spell a field differently from the other. |
| **Transport** | How one notification leaves the process, in `blackboard.delivery`. `HttpxTransport` posts it; another implementation sends it somewhere else. |
| **Lane** | One agent's queue and the worker that drains it. Each call to `HttpNotifier.to` opens one, which is what lets agents be reached at the same time. |
| **Refusal** | A delivery the agent will answer the same way next time, such as a 400. The notifier reports it rather than retrying. Distinct from a **failure**, which is worth another attempt. |
| **Adapter** | A store backed by a database the application already runs, constructed over a connection the application owns. `PostgresStore` and `MongoStore`. |
| **Conformance suite** | The tests in `tests/conformance.py` that every store implementation must pass. |
| **Record** | What the board holds. Durable where the board is a database. |

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
