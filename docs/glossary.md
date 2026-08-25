# Glossary

Every term this project uses, and what it means here. A term is defined once, in this table, and every page, docstring and identifier uses it in that sense.

## The parts

| Term | Meaning |
| --- | --- |
| **Board** | The shared record. It stores contributions, orders them, and reads none of them. Any implementation of `BoardStore` is a board. |
| **Control component** | Everything that decides: which agents are notified, whether a write is admitted, and when the run closes. `Control` in the code. |
| **Application** | The system built on this library. It supplies the agents, the content, the region declarations, and the rules. |
| **Agent** | A participant that reads the board, decides whether it has anything to add, writes, and acknowledges. It registers itself; the library never creates one. |
| **Skeletal** | Nii's term for a blackboard system that carries no domain knowledge, so an application is built on it by adding knowledge and control. |

## The record

| Term | Meaning |
| --- | --- |
| **Region** | A named part of the board holding one kind of information. Every region is a level or a register. |
| **Level** | A region that accumulates. Each write adds one contribution at the end, and nothing already there changes. Levels hold what the agents produce. |
| **Register** | A region holding one current value under a version. A register holds a premise. |
| **Contribution** | One unit written into a level. |
| **Content** | What any write carries. A contribution's content, and the content a `BoardChange` records for a write of either kind. |
| **Value** | A register's current content. `RegisterState.value`, and the `value` argument of a register write. |
| **Premise** | What a register holds. Something the work is given rather than something it concluded, so it has one correct value at a time and a later value replaces the earlier one. |
| **Conclusion** | What a level holds. Something an agent drew from evidence, which stays beside the evidence it rests on rather than replacing it. |
| **Sequence number** | A write's position in the board's total order, and its address. One counter serves every region of one board. There is no separate identifier. |
| **Version** | A register's revision count. A register write names the version it expects to replace and fails if the register has moved past it. |
| **Board identifier** | Which board a row or document belongs to, where one database holds many. `board_id`. |

## The write path

| Term | Meaning |
| --- | --- |
| **Writer** | Whoever made a write, named in every write call and every audit event. Usually an agent, and not necessarily one: an operator, a supervising component, or a scheduled job reaches a region the same way, and no write call checks that its writer registered. `agent` is used only where the caller must be registered, meaning the recipient of a notification and the caller of `ack`. |
| **Write** | Putting content into a region. A level write adds a contribution; a register write replaces a value under its version. Both take a sequence number and both report `Written`, whose version is absent on a level write because a level has none. |
| **Admission rule** | The application's function, called on every proposed write before the board sequences anything. It answers `Accept()` or `Reject(reason)`. |
| **Seed** | The initial value of every register, written when the run opens. The seed is a write: it reaches the board and takes a sequence number. It bypasses admission, because it is the application's own input rather than a proposal from a writer. |
| **Conflict** | A register write that named a version other than the current one. It changes nothing and takes no sequence number. |
| **Rejection** | A write the control component refused, with the cause. It never reaches the board and takes no sequence number. |

## Notification and closing

| Term | Meaning |
| --- | --- |
| **Notification** | The message telling one agent it is out of date. It carries a sequence range and the regions that changed, and no values. |
| **Batch window** | The interval a register waits after its first pending change, so several changes in quick succession become one notification. |
| **Acknowledgment** | An agent reporting that it has stopped working on one notification. It says nothing about what the agent found. |
| **Cursor** | An agent's last acknowledged sequence number. |
| **Subscription** | Which regions wake an agent. Omitting `subscribes_to` subscribes it to every register and to no level; naming regions subscribes it to exactly those, of either kind. |
| **Run** | One model, from creation to close. |
| **Idle limit** | How long nothing may happen before the run closes. Every write, registration and acknowledgment pushes it out. |
| **Wall clock limit** | The longest a run may last, whatever else is true. |
| **Termination predicate** | The application's function, asked when the idle limit passes, answering whether the run may close. |
| **Outcome** | How a run ended: `Settled`, `WallClockExpired`, or `Aborted`. Each names the agents that did not finish. |
| **Audit** | The control component's record that each event occurred, in order. |

## Storage

| Term | Meaning |
| --- | --- |
| **`BoardStore`** | The protocol a board implements: six methods, three that write and three that read. |
| **Adapter** | A board backed by a database the application already runs, constructed over a connection the application owns. `PostgresBoard` and `MongoBoard`. |
| **Conformance suite** | The tests in `tests/conformance.py` that every board implementation must pass. |
| **Record** | What the board holds. Durable where the board is a database. |

## Two words this project does not use

**A wake**, as a noun. What an agent receives is a notification, in the code and in the prose. The verb stays, because it names the effect rather than the thing: a register change wakes an agent, and what the agent then holds is a notification.

**A budget.** A run has two limits, both durations, and nothing countable is consumed. `RunLimits` carries them.

## One register, four words

A register and what it holds are four different things. Each has one name, and the four are not interchangeable.

| The thing | Its name | Where you meet it |
| --- | --- | --- |
| The region | a **register** | `Register("window")` |
| What it holds, considered as a role: given to the work rather than concluded by it. This is why the region is a register and not a level | a **premise** | prose only, because it is the reason for the kind rather than a thing in the API |
| Its content at this moment | the **value** | `read_register("window").value` |
| Its first value, written when the run opens | the **seed** | `create_model(seed={"window": ...})` |

Reading it as one sentence: a register holds a premise; its value is what that premise is right now; the seed is the value it started with.

What a register holds is never called a fact. Premise and conclusion are a pair on one axis, given against concluded, and "fact" names neither end of it: a conclusion an agent reaches is a fact too, so the word does not separate the two kinds of region.

## Two distinctions this project keeps

**Register the region, and registering an agent.** These are unrelated words that happen to be spelled alike, and both stay: `Register` is the blackboard literature's name for the region kind, and registering is what the literature calls adding a participant. The rule for prose is that no passage uses both senses. A region is *declared*; an agent *registers*. Where both must appear, name the region kind as `Register` and the act as "an agent registers itself".

**Complete, and settled.** The termination predicate answers `COMPLETE` about the result: the application has what it needs. `Settled` describes the run: nothing happened for the idle limit. A run settles while the predicate has never been asked, and a predicate answers complete on a run that then expires on the wall clock. They are answers to different questions and neither implies the other.
