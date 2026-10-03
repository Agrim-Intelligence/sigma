# #347 control: the guard goes red on the documented gesture

Measured on a scratch copy of `skills/` and `hooks/` taken at base commit `4c8562f` with the #347 tree, using exactly the
command `docs/launch/shared-sdlc-paths.md` gives:

```sh
python3 tools/readiness/shared_paths.py check --sigma <scratch> --fixture tests/fixtures/predecessor_written_paths.json
```

| Scratch state | Exit | Output |
| --- | ---: | --- |
| unmodified | 0 | none |
| a new function in `skills/agrim-init/scripts/setup_wizard.py` writes `state/setup-wizard-dismissed.json` | 2 | `new Sigma writer skills/agrim-init/scripts/setup_wizard.py::_control_extra_writer is not in the vetted_writers of the entry for .sdlc/state/setup-wizard-dismissed.json` |
| unmodified, with the fixture's entry for that path removed | 2 | `Sigma writes it (skills/agrim-init/scripts/setup_wizard.py::write_dismissed) and the predecessor writes .sdlc/state/setup-wizard-dismissed.json; no entry` |

`tests/test_shared_sdlc_paths.py` repeats the first two rows through a subprocess and the third in process, so the green
and the red are both pinned. What the control does not show: a second write inside an already vetted function, or a writer
the static scan cannot resolve, is not refused (stated in the doc).
