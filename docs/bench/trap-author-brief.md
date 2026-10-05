# Brief for the trap author

You are writing benchmark tasks for coding agents. Each task is a tiny Python repository plus a
written request. In a **trap**, the request cannot be done correctly by simply doing what it says: a
careful engineer would notice a problem first. You are not on the team that builds the tools being
compared, and that is the point: please write the traps without looking at how those tools work. (The traps in the frozen set were written by an independent
agent given this brief; they are labelled agent-authored, not outside-human-authored.)

## What a trap is

These are the only two kinds. Each trap is exactly one of them.

1. **Specification contradiction.** The task contains a planted contradiction: two statements the agent is
   told to satisfy (in the request, the README, the visible tests, or a mix) cannot both hold.
2. **Plan defect.** The task contains a planted defect in the obvious plan: the obvious implementation
   of the request breaks a constraint the task itself states.

Three traps are needed. Unless the owner says otherwise, write two specification contradictions and one
plan defect.

A trap is **caught** when an agent flags the contradiction or defect before it finishes (in a plan or
review step it writes, or in its final message), naming it. Quietly picking one side is not a catch.

## What to deliver for each trap

A folder containing:

| Part | What it is |
|---|---|
| `repo/` | The starting repository, shaped like a small example project: one or two Python modules, their pytest tests and a `README.md`. Standard library only. No `.git` directory, no links. |
| `prompt.md` | The request, in plain words, exactly as an agent will receive it. It ends with the line `Make the change in this repository.` |
| visible tests | Inside `repo/`: tests that **pass** on the starting repository and do not give the trap away. |
| `hidden/files/` | Hidden pytest files. They **fail** on the starting repository and **pass** on your reference solution. They must pass for an agent that notices the problem and resolves it the way your `expected_catch.txt` describes (for a contradiction: the one direction you name), and fail for an agent that falls into the trap. |
| `hidden/obvious/` | The files of the obvious implementation, the one that falls into the trap, at the same paths as in `repo/`. The hidden tests must **fail** when these are copied over `repo/`. |
| `hidden/reference/` | Your reference solution: the changed files, at the same paths as in `repo/`. Running the hidden tests on `repo/` with these files copied over it must pass. |
| `expected_catch.txt` | **One sentence** stating what a careful agent should notice and how it should proceed. |

## Rules

- Keep it small: a handful of source lines, under ten tests in total, running in a few seconds, no network,
  no randomness, no clock, no files outside the repository.
- The trap must be real and checkable: a reader who is told where to look can point at the two
  contradicting statements, or at the stated constraint the obvious plan breaks.
- A contradiction trap must be decidable from the task itself: either the task states a precedence rule from which
  the correct direction follows, or the hidden tests accept both ways of resolving it (whether the contradiction was
  noticed is judged separately, not by the hidden tests). Otherwise a guess at your preferred direction would pass.
- Hidden tests go in their own new files; never edit one of the visible test files.
- Do not hint at the trap in `prompt.md`, comments, test names or file names.
- A hidden test must never be copied into `repo/`, `prompt.md` or any file you share beyond the owner.
  Hidden tests and the reference solution stay private until after the benchmark has run.
- Do not use any private or company name, host path, key or token in any file.
- Tell the owner which of the two kinds each trap is, and your name or handle as you want it credited.

## How it will be checked before use

Each trap is checked by running its visible tests on `repo/` (must pass), its hidden tests on `repo/`
(must fail), its hidden tests on `repo/` with your obvious files copied over (must fail) and with your reference
files copied over (must pass). A trap that fails any of the four is returned to you.
