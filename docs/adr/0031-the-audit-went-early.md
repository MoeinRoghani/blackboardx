# ADR 0031: The audit was removed before its date

Date: 2026-09-05

## Status

Accepted. Records a deliberate exception to the deprecation rule in `CLAUDE.md`.

## Context

`Control.read_audit` and six event classes were deprecated with a removal date of 2026-12-05, under the rule that nothing in `__all__` is deleted in one step.

They were removed the same day they were deprecated, twelve hours later. That is a breach of a published window, and a window that quietly evaporates is worse than no window, because the next one means nothing.

## Decision

Remove it, and record here that the window was cut short and why.

Two things decided it.

**The deprecation was twelve hours old.** Nothing could have been built against it, so the window protected nobody.

**The audit was not dead surface, it was live cost.** It built one object per write, without bound, in every run of a released version, feeding a reader nothing else consulted. A thousand writes built a thousand objects for nobody.

`attach_model` keeps its date. It has a replacement an application may be mid-migration on and it costs nothing to carry. The audit cost memory, and that asymmetry is the whole argument.

## Consequences

What the audit recorded is answered without it, and every one of those answers predates this change. A contribution carries its writer and the instant, `store.read_agents` says how far each agent was told and answered, `store.read_run` says the outcome, and a rejection is returned to its caller. The audit had become a second history beside a record that answered the same questions.

`docs/migrating.md` carries a section of its own saying the window was cut short, rather than letting the date disappear.

The rule in `CLAUDE.md` is unchanged. This is an exception with its reasons written down, not a precedent: the test it passed is that the deprecation was hours old and the thing being removed had a running cost. A deprecation that anyone could have built against runs its window.
