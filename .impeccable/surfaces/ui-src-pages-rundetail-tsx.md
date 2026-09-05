---
version: 1
slug: "ui-src-pages-rundetail-tsx"
primary_target: "ui/src/pages/RunDetail.tsx"
related_targets: ["ui/src/pages/Runs.tsx","ui/src/pages/Overview.tsx","ui/src/pages/Agents.tsx"]
---

## Direction contract

THESIS: A run is a page of engraved score. Agents are staves, the sequence is
bar numbers, and silence is what ends a run. It refuses the observability
dashboard and the waterfall alike: no tiles, no stacked spans, no left rail of
chrome beside the data.

OWN-WORLD: Score paper, engraver's black, Edition Peters livery green for
structure, conductor's red pencil for refusals. Archivo with real italics for
expression marks, tabular lining figures for bar numbers. Braces, barlines and
multi-bar rests carry grouping; there are no cards and no boxes.

STORY: The operator reads a run the way a conductor reads a page, sees which
voice entered where and which stayed silent, and finds the bar the trouble
started in.

FIRST VIEWPORT: A braced system spanning the content width. Agent names set
condensed in the left margin, bar numbers above the top stave, noteheads at
each write, a boxed rehearsal mark where a premise changed, red pencil through
refusals with the rule's reason set italic beneath. Systems wrap down the page.
The playhead is the primary control.

FORM: The Full Score. Candidate 1 of 7 on the grounded list, chosen by the user
over the assigned roll. Seed key a3231bfa.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
