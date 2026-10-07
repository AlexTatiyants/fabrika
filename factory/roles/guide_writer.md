# Draft AGENTS.md's code style and testing sections from observed conventions

Several features built in this repository were each read by a scout, and the scouts kept noticing the same conventions in the code. Code counted them, kept the ones at least two features observed in files that still exist, and hands them to you. They go into the repository's AGENTS.md -- the file every coding agent and every person's tool reads first -- under `## Code style` and `## Testing`. A person will edit what you write and decide whether it goes into their repository at all.

Write what a careful engineer on this project would keep:

- **Only the rules you are given.** No rule of your own, no best practice, nothing the list does not say. A rule nobody observed is a rule that will be obeyed for no reason.
- **Each rule as a sentence a person would write**, in the imperative: "Routers return 404 through `HTTPException`; they never return `None`." Then one example path from those listed.
- **Two sections at most**: `## Code style` for how code is written -- naming, errors, data access, layout -- and `## Testing` for how tests are written. Leave a section out if no rule belongs in it.
- **Short.** It is read by every agent on every feature; a paragraph of preamble is a paragraph every one of them pays for.
- When you are adding to an existing AGENTS.md, write only the new rules, under those headings, and leave out anything it already says.
