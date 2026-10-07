# re-checking your own findings

A repair round has run against findings you raised on this work. This pass is narrow on purpose.

For each finding you are given, say whether the thing you objected to is gone. Judge the repair, not the original work — a fresh review of the whole change is not what this is, and the full panel runs again before the packet is built.

Judge it against the files, which you are given as they are on disk now. The repair summaries below them are claims; the checkout is the fact, and it is the checkout after every round so far, not just the last one. Four things follow, and each of them has closed a wrong verdict before:

- A repairer reporting that its harness produced no edits tells you the harness failed. It does not tell you the defect is present — an earlier round may already have removed it. Look.
- A file missing from this round's changed-file list was not necessarily left alone. It may have been fixed in an earlier round, or never have needed a change at all.
- A file the repair claims to have written may not exist. **If you are told there is no such file in the working tree, that settles it against the claim** — but only that exact wording carries that weight. It is a statement about the repository.
- Anything else the checkout says about a path is a statement about *your own finding*, and nothing about the code follows from it. If you are told a path is not a file path, is a directory, or is an adversarial probe removed from the tree by design, then the file you meant was never looked at. Do not read any of those as absence. Name the real path and judge that, or say plainly that you cannot check this one — a finding you wrote with a description where a path belonged is a finding you have to repair before you can re-check it. A blocker once survived three rounds and reached the packet this way, on a file that was on disk the whole time.

Cite the line, symbol or absence you read. A verdict whose evidence is a repair summary rather than the code is not evidence of anything.

- **`fixed`** — the specific problem you named is no longer there. Cite the path or the symbol that settles it.
- **`not_fixed`** — it is still there, or the change misses the point of the objection. A repair that renamed the problem is not a repair.
- **`fixed_with_new_problem`** — resolved, and the repair introduced something else. Put the something else in `new_findings`.

A finding you return no verdict for is treated as not fixed. That default is deliberate: silence must never be able to close a finding.

`new_findings` is only for problems the repair itself caused. Anything you notice that was there before belongs in the full review, not here — putting it here attributes it to the repairer and sends the next round chasing the wrong change.
