# sigma-goal-review selection details

description: "The Product-stage confirmation gate — independently confirm a `goal-design` write-up (maker-is-never-the-checker), then produce the Spec/Epic ticket and its children and write the `sdlc:designed` overlay, only once confirmed. Use when the user runs /sigma-goal-review, when a `.sdlc/design/<n>.md` write-up is ready to be confirmed, or when a filed \"Design #N\" meta-issue's own design pass has just completed and needs sign-off before the target can proceed."
allowed-tools: Bash(python3 *), Bash(git *), Bash(mkdir *), Bash(gh auth switch *), Bash(gh auth status *), Bash(gh issue view *), Bash(gh issue comment *), Bash(gh label list *)

These fuller triggers are kept outside the always-listed description. Read the
skill's main instructions before acting.
