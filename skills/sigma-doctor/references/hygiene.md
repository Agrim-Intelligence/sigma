# Standing-doc hygiene

Report it as a **separate section** from the setup checks and never fold it into the ready score —
"is my setup working?" and "are my docs rotting?" are different questions with different fixes. This
half is deliberately mechanical: it only reports references that provably don't resolve, or that
resolve *outside* the repository — a leading `/`, a `..`, or a symlink that leaves the tree is the
same failure for a doc making claims about THIS repo. The judgment
half — demoting a rule that CI now enforces, archiving a superseded plan — belongs to **`sigma-retro`**,
which proposes standing-doc changes and parks them for your approval rather than editing them.
