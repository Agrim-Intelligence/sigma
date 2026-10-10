# #822 controls (each guard broken, seen red, restored, seen green)

Gesture: `$HOME/.sigma-venv312/bin/python -m pytest tests/test_prd_intake.py::<id> -q -p no:cacheprovider` from the worktree. C4 runs the line copied from SKILL.md.

| control | what was broken | red test + failing line | restored |
|---|---|---|---|
| C2 quote-in-PRD check | `if qn not in prd_norm:` -> `if False:` | test_invented_quote_refused : assert (0 == 2) | green |
| C2c cite required | `if not quotes:` -> `        raise Refused("uncited-answer"` | test_answer_without_cite_refused : assert (0 == 2) | green |
| C2b min quote length 0 | `MIN_QUOTE_CHARS = 20` -> `MIN_QUOTE_CHARS = 0` | test_one_char_quote_refused : assert (0 == 2) | green |
| C3 open_ write deleted | `quoted = " / ".join('"%s"' % q for q in quotes)` -> `        continue` | test_planted_contradiction_becomes_open_question : KeyError: 'constraints' | green |
| C4 repo guard removed (documented gesture) | `prd = check_prd_path(a["--prd"], os.getcwd(), sdlc)` -> `prd = pathlib.Path(a["--prd"]).resolve()` | test_docs_gesture_matches_skill_md : AssertionError: assert (2 == 2 and 'prd-too-large' == 'repo-source' | green |
| C4b guard only for tracked files | `if p.suffix.lower() not in DOC_SUFFIXES or rel.parts[0] in CODE_DIRS:` -> `    if (p.suffix.lower() not in DOC_SUFFIXES or rel.parts[0] in CODE_D` | test_untracked_py_in_repo_refused : AssertionError: new.py | green |
| C5 reject PRD without a heading | `sha = hashlib.sha256(raw).hexdigest()` -> `    sha = hashlib.sha256(raw).hexdigest()` | test_plan_accepts_any_shape[flat.txt] : AssertionError: intake.py: REFUSED [no-heading]: x | green |
| C6 render ignores prd_source | `if prd_source:` -> `        lines += _source_lines` | test_source_block_carries_path_and_sha : AssertionError: assert '### Source' in '<!-- sigma:dossier-qa:start -->\n## Dossier\n\n- **title** — T\n\n<!-- | green |
| R5 defang skipped | `def _defang(text):` -> `    return " ".join(str(text).split())` | test_source_block_trigger_words_defanged : AssertionError: assert [('Needs', '5...nds on', '7')] == [] | green |
| C7 placeholders counted as cited | `quoted = " / ".join('"%s"' % q for q in quotes)` -> `        cited += 1` | test_placeholder_not_counted_as_cited : assert 8 == 5 | green |
| CU umbrella omits last Dossier | `for name, num in filed.items()]` -> `for name, num in list(filed.items())[:-1]]` | test_umbrella_lists_every_dossier : assert '#2' in '---\nid: 0003\ntitle: "PRD intake: messy.md (2 Dossiers)"\nlane: auto\nstatus: proposed\n---\n | green |
| CU2 umbrella gains a blocker phrase | `lines += ["- #%s %s"` -> `lines += ["- needs #%s %s"` | test_umbrella_body_has_no_phantom_blocker : AssertionError: assert [('needs', '1...'needs', '2')] == [] | green |
| CR .filed.json never written | `_write_json(record_path, {"dossiers": filed, "umbrella": record.get("u` -> `pass` | test_rerun_after_partial_failure : AssertionError: assert 4 == 3 | green |
| CR2 resume keyed by PRD hash | `return hashlib.sha256(pathlib.Path(plan_path).read_bytes()).hexdigest(` -> `return json.loads(pathlib.Path(plan_path).read_text())["prd"]["sha256"` | test_replan_new_answers_fresh_resume : AssertionError: assert '1cc8c0d33a08' != '1cc8c0d33a08' | green |
| R1b decision continue vs next_step | `DECISION = dossier.DECISIONS[NEXT_STEP]` -> `DECISION = "continue"` | test_next_step_matches_decision : AssertionError: {'outcome': 'failed', 'detail': "decision='continue' does not match the recorded next_step ans | green |
| R1c open_ question entries dropped | `{"id": k, "ask": v} for k, v in qs.items()]` -> `{"id": k, "ask": v} for k, v in qs.items() if False]` | test_every_open_has_question : AssertionError: assert '' | green |
| R1d followup cap removed | `if len([k for k in answers if k.startswith(dossier.FOLLOWUP_PREFIX)]) ` -> `if len([k for k in answers if k.startswith(dossier.FOLLOWUP_PREFIX)]) ` | test_followup_over_cap_refused : assert (0 == 2) | green |
| R3 duplicate outcome check removed | `if len(set(names)) != len(names):` -> `if False:` | test_duplicate_outcome_names_refused : assert (0 == 2) | green |
| CR1-1a PRD-path walk-up removed | `_find_root(p.parent),` -> `` | test_repo_prd_from_non_repo_cwd_and_sdlc_refused : assert (2 == 2 and 'answers-unreadable' == 'repo-source') (test_prd_intake.py:494) | green |
| CR1-1b install-root removed | `SELF_ROOT.resolve()) if r]` -> `) if r]` | test_install_root_guards_without_git (test_prd_intake.py:519) | green |
| CR1-2 text-doc allow-list everywhere | `if p.suffix.lower() not in DOC_SUFFIXES:` (prd-not-text-doc) -> `if False:` | test_non_text_doc_outside_repo_refused[credentials|config.json|key.pem|notes.md.bak] (4 red) | green |
| CR1-4 open_ without status counts | `if st is None and key.startswith(OPEN_PREFIX):` -> `if False:` | test_open_answer_without_status_not_cited : assert (0 == 2) (test_prd_intake.py:538) | green |
| CR2-1 code-dir compare case-sensitive | `rel.parts[0].casefold() in CODE_DIRS` -> `rel.parts[0] in CODE_DIRS` | test_code_dir_match_is_case_insensitive[Skills/..., TESTS/..., Hooks/..., EVALS/..., Tools/...] (5 red; lowercase + nested cases stay green) | green |
