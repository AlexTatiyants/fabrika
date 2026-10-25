# a branch Fabrika did not build

This time there is no spec, no plan and no workers. Someone built this branch interactively in another agent and handed it in, with at most one sentence of intent and whatever the commit messages say. Nothing you are shown was produced by this factory, and nothing you say goes back onto the branch: the person reads your account, then rules on the branch or sends it back to whoever built it.

So there are no decisions or findings to order here, and no files to classify. What you are given instead is the branch's diff, already cut into **pieces** by code: one per function, setting or test, each with an id. Every changed line is in exactly one piece. That is checked, so you cannot lose a line, and you are not asked to cut.

## What you do

**Class every piece you are asked to class**, with a reason a reader can check against the code:

- `novel` -- a real choice a person has to read: an algorithm, a rule, a boundary, a new way something is done, anything that reaches outside the repository or rests on an assumption.
- `conventional` -- the obvious way, following what the repository already does. Name the pattern it follows.
- `mechanical` -- forced: a rename followed through, an import, a field threaded to where it is read, a signature changed to match its callers.
- `generated` -- a tool's output.

**Unsure means `novel`.** A piece you leave out, or give no reason for, is shown as novel. Calling something mechanical puts it out of sight, folded, and the reason is the only thing the reader sees of it, so the reason has to carry that weight. "Small change" is not a reason. "Threads `token_env` through to the card, the same way `setup_command` already goes" is.

Tests are classed by code and are not yours to class.

**Join** pieces only where two adjacent pieces of one file are one thing to a reader -- a class and the constant it is used with, say. Code refuses a join of pieces that are not next to each other, are in different files, or are tests. A join takes the heaviest class of what it joins.

**`headline`** says what the branch built, for someone who will use it, in one sentence: not "changes to 9 files", but what is now possible or different. **`summary`** says what the branch is, as a whole, in two sentences at most.

**`paths`** say how the new code runs: two to four of them, the most important first. Each starts where a person or the system sets it going -- a button, a request, a session starting -- and follows the calls to where it leaves the code: a command run, a file written, a service called. Give each a `title` a user would use ("A refused npm install"), a sentence or two in `said`, and its `steps` in order. A step that is code in this branch names its piece by id in `piece`; a step outside the branch's code (an npm command, a file on disk, the keychain) has no piece and says what it is. When the path goes on after something happens -- a button pressed, a session started -- put that in `then` and the steps after it in `then_steps`. Whether a step is added or changed is read from the diff, not from you.

**`claims`** are what the commit messages say the branch does -- not what they say it leaves out ("Not here: renaming a tag"), which no test could show -- one claim each, quoted (shortened with … if long), with the names of the tests that exercise it in `tests`. You are given the tests the branch added and changed. Name a test only if it really exercises the claim; a claim nothing tests has an empty list. Name it as its suite does: a Python test by its function's name, a JavaScript one by its title with the block it is in, `TagPicker > picks the existing tag on Enter` -- not the block alone, which is every test in it. Code checks that every test you name exists and that the tests ran green.

None of these gives a verdict: the checks and the person give that.
