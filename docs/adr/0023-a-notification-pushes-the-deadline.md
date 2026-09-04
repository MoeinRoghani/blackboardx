# ADR 0023: Handing an agent work pushes the idle deadline out

Date: 2026-09-04

## Status

Accepted. Completes ADR 0009 on which events are measured, and states in the code what that record said in prose.

## Context

ADR 0009 measures silence from the last event, and names writes, registrations and acknowledgments as events. The code pushed the deadline out on a write, on a premise write, on an acknowledgment, on the opening premise writes, and at the close of a batch window. It did not push it on a registration.

A registration hands the agent a notification covering every subscribed region that already holds something, because an agent that has just joined is out of date with the whole board. With an idle limit of ten minutes, an agent joining at nine minutes and fifty nine seconds was given that notification and the run closed one second later, naming it unfinished. The run declared an agent unfinished for work it was given no time to do.

The two dispatch paths also disagreed. A notification released by a closing batch window pushed the deadline, because `_close_window` records an event after dispatching. A notification released by a registration pushed nothing.

`register_agent` carries a comment reading "Registering never completes a run. An agent joining is the start of work, not the end of it." That is a decision about closing a run, and not re-arming the deadline is a different thing, so the comment left the disagreement in place rather than settling it.

## Decision

Dispatching a notification pushes the idle deadline out. The event is the notification rather than the registration, because that is the property the run needs: a run that has just given an agent something to do does not then declare the work over.

Naming the notification rather than the registration settles the case ADR 0009 leaves open. An agent that subscribes to nothing the board holds is handed no notification, and the deadline stays where it was, because nothing was given to that agent to do.

Every other path is unchanged. A write pushed the deadline out before reaching the dispatch, an acknowledgment pushes it, and the close of a batch window pushed it already.

## Consequences

An agent joining late is answered. The run stays open for the idle limit measured from the notification it was handed, so a late arrival gets the same time to work as an agent that was there from the start.

A run whose agents keep registering stays open while they do. The exposure is the same as a run whose agents keep writing, and the wall clock bounds both.

ADR 0009's list of events reads as it did, and a registration pushes the deadline whenever it hands out a notification, which is every case that matters to it.

`docs/guides/ending-a-run.md` now names the notification among the events, and says that a registration handing out nothing pushes nothing.
