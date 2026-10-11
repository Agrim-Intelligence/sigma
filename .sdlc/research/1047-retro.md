# #1047 retro
- Learned: the runner's wall time was in no existing CI log (grep of three green main job logs), so "three measured runs" cannot precede the mechanism; ceiling lands in a follow-up commit on the same PR after three PR CI runs.
- Debt: ceiling is only as good as three samples on one runner type (macOS 3.12 PR leg); the weekly full matrix is not measured. Full-suite run under -n 4 had one load-sensitive failure (tests/test_doctor.py::test_wizard_status_is_fast_with_a_hanging_ls_remote, 27s vs 3s alone), unrelated.
