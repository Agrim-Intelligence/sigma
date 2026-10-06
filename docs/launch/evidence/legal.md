# Legal and naming: fact sheet for counsel (D13, issue #343)

## 1. Status of this page

This is a FACT SHEET assembled by an agent on 2026-10-06. It is not legal advice and contains no legal conclusion.
Where a decision belongs to the owner or to counsel, the cell reads `AWAITING COUNSEL` and has been left as a marked
placeholder on purpose. Counsel has been engaged by the owner and has NOT answered. D13 (`docs/launch/review-plan.md`) is therefore not
scoreable, and nothing on this page makes it so.

What every fact below rests on is named beside it: a file and line in this tree at commit `542a8bf`, a registry response
read on the date given, or a file inside a published package. Anything not checked says so. A "not found" means a public,
read-only query returned nothing on the date and with the method stated; it does not mean the name is free in any legal
sense.

## 2. Owner decisions recorded (not legal decisions)

| Date | Decision | Source |
|---|---|---|
| 2026-10-06 | Product display name: `Sigma Loop` | owner instruction to the agent for issue #343 |
| 2026-10-06 | Repository and install id: `sigmaloop` | same |

The tree already carries these names: `.claude-plugin/plugin.json:2` and `.claude-plugin/marketplace.json:2,9` name the plugin
`sigmaloop`; `README.md:1` is titled "Sigma Loop". The skill and command family is still prefixed `sigma-` (for example
`skills/sigma-loop/`, `sigma-doctor`): that prefix was not part of the decision and is recorded here as a fact only.

## 3. Licence facts about this repository

| Fact | Where | Verified how |
|---|---|---|
| Licence is MIT | `LICENSE:1` | read the file |
| Copyright line is "Copyright (c) 2026 Agrim Intelligence" | `LICENSE:3` | read the file |
| Plugin manifest licence is `MIT` | `.claude-plugin/plugin.json:8` | read the file |
| Plugin manifest author is an individual, "Swapnil Dubey", not the company in `LICENSE:3` | `.claude-plugin/plugin.json:5-7`; `.claude-plugin/marketplace.json:4-6` (owner) | read the files |
| Git history in this repository starts 2026-09-29 and has 128 commits under two author names that share one email address | `git log` | read the log |
| No assignment, contributor agreement, CLA or DCO document is in the tree | whole tree | searched file names; absence in the tree says nothing about whether one exists elsewhere |
| A `NOTICE` file did not exist before this change | whole tree | `git ls-files` |

## 4. Dependencies and their licences

How each row was verified is in 4.4. "Declared" means the package's own metadata field. "Read" means the licence file inside
the package artifact was opened and its text and copyright line read; for every Python row below the file was MIT licence
text (the permission and warranty paragraphs were present).

### 4.1 Shipped code

Shipped Python (every tracked `*.py` outside `tests/` and files named `test_*.py` or `conftest.py`) imports only the standard
library and one optional third-party module, `slack_sdk`. The scan is `tests/test_third_party_imports.py`, an AST scan of
`import` and `from` statements. Dynamic imports, shell and TypeScript are outside it; its docstring lists the limits.

| Dependency | Version checked | Declared licence | Licence file read | Copyright line in that file | Role | Where |
|---|---|---|---|---|---|---|
| `slack-sdk` (import `slack_sdk`) | 3.45.0 (latest on PyPI 2026-10-06; the code pins no version) | MIT (`License: MIT`, classifier MIT) | `slack_sdk-3.45.0.dist-info/licenses/LICENSE` in the wheel | "Copyright (c) 2015- Slack Technologies, LLC" | optional; the user installs it by hand; imported lazily inside two functions | `skills/sigma-loop/scripts/slack_commands_listen.py:1612`, `:1691`, `:1692`; install text at `:228` and `skills/sigma-loop/SLACK_COMMANDS.md:111` |
| `@modelcontextprotocol/sdk` (TypeScript, not Python) | 1.30.0 (the lock) | MIT | `package/LICENSE` in the npm tarball | "Copyright (c) 2024 Anthropic, PBC" | optional; the user installs it with Bun for the autowatch channel | `skills/sigma-loop/channels/sigma-autowatch/package.json` (range `^1.0.0`), `bun.lock`, `webhook.ts` |

Facts that may matter to the owner, recorded without comment: (a) `SLACK_COMMANDS.md:111` and the error text at
`slack_commands_listen.py:228` tell the user to install `slack_sdk[socket-mode]`; the 3.45.0 wheel metadata declares one
extra only (`optional`), so `socket-mode` is not an extra of that release. (b) The `optional` extra of `slack-sdk` would add
`aiohttp`, `aiodns`, `websockets`, `websocket-client`, `SQLAlchemy` and `boto3`; the repository does not ask for it.

### 4.2 Test-only dependencies (not shipped; named in `CONTRIBUTING.md:12`)

| Dependency | Version checked | Declared licence | Licence file read | Copyright line in that file | Used by |
|---|---|---|---|---|---|
| `pytest` | 9.1.1 | MIT (`License-Expression: MIT`) | `pytest-9.1.1.dist-info/licenses/LICENSE` in the wheel | "Copyright (c) 2004 Holger Krekel and others" | the test suite; `.github/workflows/ci.yml:43` |
| `pytest-xdist` | 3.8.0 | MIT (`License-Expression: MIT`) | `pytest_xdist-3.8.0.dist-info/licenses/LICENSE` in the wheel | "Copyright (c) 2010 Holger Krekel and contributors." | `-n 4` in `CONTRIBUTING.md:18` |
| `PyYAML` | 6.0.3 | MIT (`License: MIT`, classifier MIT) | `LICENSE` in the source archive, identical (same sha256) to `pyyaml-6.0.3.dist-info/licenses/LICENSE` in the macOS wheel | "Copyright (c) 2017-2021 Ingy döt Net" and "Copyright (c) 2006-2016 Kirill Simonov" | `tests/test_skill_frontmatter_yaml.py` (loaded with `pytest.importorskip`); `.github/workflows/ci.yml:60` |

Runtime requirements those test tools declare, metadata only (the licence files were NOT read for these): `pluggy` 1.6.0 MIT;
`iniconfig` 2.3.0 MIT; `packaging` 26.3 `Apache-2.0 OR BSD-2-Clause`; `pygments` 2.21.0 `BSD-2-Clause`; `execnet` 2.1.2 MIT;
and, on older Pythons or Windows only, `exceptiongroup` 1.3.1 MIT, `tomli` 2.4.1 MIT, `colorama` (not queried).

### 4.3 The npm channel's full lock closure

`skills/sigma-loop/channels/sigma-autowatch/bun.lock` pins 93 packages (the direct dependency above plus its transitive
closure). The licence column is the `license` field of each package's registry metadata for that exact version; the licence
files were NOT read for the 92 transitive packages. `content-type` 2.1.0 appears twice below because the lock holds two nested copies (under `body-parser` and `type-is`); the 93 lock entries are 92 distinct name and version pairs. Declared licences by count: MIT 83, ISC 7, BSD-3-Clause 2, BSD-2-Clause 1.
Nothing here is shipped as a bundle: the user installs these with Bun (`README.md` of the channel).

| Package | Version | Declared licence |
|---|---|---|
| `@hono/node-server` | 2.1.1 | MIT |
| `@modelcontextprotocol/sdk` | 1.30.0 | MIT |
| `accepts` | 2.0.0 | MIT |
| `ajv` | 8.20.0 | MIT |
| `ajv-formats` | 3.0.1 | MIT |
| `body-parser` | 2.3.0 | MIT |
| `bytes` | 3.1.2 | MIT |
| `call-bind-apply-helpers` | 1.0.2 | MIT |
| `call-bound` | 1.0.4 | MIT |
| `content-disposition` | 1.1.0 | MIT |
| `content-type` | 1.0.5 | MIT |
| `content-type` | 2.1.0 | MIT |
| `content-type` | 2.1.0 | MIT |
| `cookie` | 0.7.2 | MIT |
| `cookie-signature` | 1.2.2 | MIT |
| `cors` | 2.8.6 | MIT |
| `cross-spawn` | 7.0.6 | MIT |
| `debug` | 4.4.3 | MIT |
| `depd` | 2.0.0 | MIT |
| `dunder-proto` | 1.0.1 | MIT |
| `ee-first` | 1.1.1 | MIT |
| `encodeurl` | 2.0.0 | MIT |
| `es-define-property` | 1.0.1 | MIT |
| `es-errors` | 1.3.0 | MIT |
| `es-object-atoms` | 1.1.2 | MIT |
| `escape-html` | 1.0.3 | MIT |
| `etag` | 1.8.1 | MIT |
| `eventsource` | 3.0.7 | MIT |
| `eventsource-parser` | 3.1.1 | MIT |
| `express` | 5.2.1 | MIT |
| `express-rate-limit` | 8.6.2 | MIT |
| `fast-deep-equal` | 3.1.3 | MIT |
| `fast-uri` | 3.1.5 | BSD-3-Clause |
| `finalhandler` | 2.1.1 | MIT |
| `forwarded` | 0.2.0 | MIT |
| `fresh` | 2.0.0 | MIT |
| `function-bind` | 1.1.2 | MIT |
| `get-intrinsic` | 1.3.0 | MIT |
| `get-proto` | 1.0.1 | MIT |
| `gopd` | 1.2.0 | MIT |
| `has-symbols` | 1.1.0 | MIT |
| `hasown` | 2.0.4 | MIT |
| `hono` | 4.13.3 | MIT |
| `http-errors` | 2.0.1 | MIT |
| `iconv-lite` | 0.7.3 | MIT |
| `inherits` | 2.0.4 | ISC |
| `ip-address` | 10.5.0 | MIT |
| `ipaddr.js` | 1.9.1 | MIT |
| `is-promise` | 4.0.0 | MIT |
| `isexe` | 2.0.0 | ISC |
| `jose` | 6.2.9 | MIT |
| `json-schema-traverse` | 1.0.0 | MIT |
| `json-schema-typed` | 8.0.2 | BSD-2-Clause |
| `math-intrinsics` | 1.1.0 | MIT |
| `media-typer` | 1.1.1 | MIT |
| `merge-descriptors` | 2.0.0 | MIT |
| `mime-db` | 1.54.0 | MIT |
| `mime-types` | 3.0.2 | MIT |
| `ms` | 2.1.3 | MIT |
| `negotiator` | 1.0.0 | MIT |
| `object-assign` | 4.1.1 | MIT |
| `object-inspect` | 1.13.4 | MIT |
| `on-finished` | 2.4.1 | MIT |
| `once` | 1.4.0 | ISC |
| `parseurl` | 1.3.3 | MIT |
| `path-key` | 3.1.1 | MIT |
| `path-to-regexp` | 8.4.2 | MIT |
| `pkce-challenge` | 5.0.1 | MIT |
| `proxy-addr` | 2.0.7 | MIT |
| `qs` | 6.15.3 | BSD-3-Clause |
| `range-parser` | 1.3.0 | MIT |
| `raw-body` | 3.0.2 | MIT |
| `require-from-string` | 2.0.2 | MIT |
| `router` | 2.2.0 | MIT |
| `safer-buffer` | 2.1.2 | MIT |
| `send` | 1.2.1 | MIT |
| `serve-static` | 2.2.1 | MIT |
| `setprototypeof` | 1.2.0 | ISC |
| `shebang-command` | 2.0.0 | MIT |
| `shebang-regex` | 3.0.0 | MIT |
| `side-channel` | 1.1.1 | MIT |
| `side-channel-list` | 1.0.1 | MIT |
| `side-channel-map` | 1.0.1 | MIT |
| `side-channel-weakmap` | 1.0.2 | MIT |
| `statuses` | 2.0.2 | MIT |
| `toidentifier` | 1.0.1 | MIT |
| `type-is` | 2.1.0 | MIT |
| `unpipe` | 1.0.0 | MIT |
| `vary` | 1.1.2 | MIT |
| `which` | 2.0.2 | ISC |
| `wrappy` | 1.0.2 | ISC |
| `zod` | 4.4.3 | MIT |
| `zod-to-json-schema` | 3.25.2 | ISC |

### 4.4 How these were verified (2026-10-06)

- Python: for each package the PyPI JSON API (`pypi.org/pypi/<name>/json`) gave the version, `license` or
  `license_expression`, classifiers and `requires_dist`. The wheel (the sdist for `PyYAML`'s second check) was downloaded to a
  scratch directory, its sha256 compared with the digest PyPI publishes (all matched), and the licence file read from inside
  it. Nothing was installed or executed.
  Wheel sha256: `slack_sdk-3.45.0-py2.py3-none-any.whl` `6356d4486d1a3ad156462c5544ab1b9c076ff426a250495c08f51b7ad71eb8fb`;
  `pytest-9.1.1-py3-none-any.whl` `37a86b45efb9a47a61a36449063e8e18d0cab3161329fc099eb21783169c4f0c`;
  `pytest_xdist-3.8.0-py3-none-any.whl` `202ca578cfeb7370784a8c33d6d05bc6e13b4f25b5053c30a152269fd10f0b88`;
  `pyyaml-6.0.3.tar.gz` `d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f`.
- npm: the registry document `registry.npmjs.org/<name>/<version>` gave `license` and `dist.integrity`; every one of the 93
  integrity values equals the value in `bun.lock`. For `@modelcontextprotocol/sdk` the tarball was downloaded, its sha512 matched
  the registry integrity, and `package/LICENSE` was read.
- The import scan: a first pass with Python 3.12 over `git ls-files '*.py'` found two non-stdlib roots outside local siblings:
  `slack_sdk` (shipped) and `pytest` (only in `tests/` and in `test_*.py` fixtures of the benchmark task repos). That is the
  rule `tests/test_third_party_imports.py` now enforces.
- Not done: licence files of transitive Python and npm packages were not read; no software composition tool was run; the
  licences of Python packages a user may pull in by hand were not examined beyond 4.1 and 4.2; no check was made of what
  the host products (Claude Code, Codex, Cursor, GitHub CLI, Bun, Python itself) are licensed under.

## 5. Provenance

| Item | Fact or placeholder |
|---|---|
| The owner's statement (issue #343): this project is the rebrand of an earlier plugin by the same owner | stated by the owner; not independently verified by the agent |
| Evidence in the tree that an earlier plugin existed | the fixture `tests/fixtures/predecessor_written_paths.json` and an old-plugin constant at `skills/sigma-doctor/scripts/doctor.py:1829`; the earlier plugin's name and repository are deliberately not written in this tree |
| The predecessor's licence file | NOT READ by the agent for this sheet. No licence term of the predecessor is stated anywhere in this change |
| Copyright holder of this repository's code (company in `LICENSE:3`, individual in the manifests) | AWAITING COUNSEL |
| Who owns the predecessor's code and under what licence it was written | AWAITING COUNSEL |
| Whether relicensing the ported parts as MIT needs any consent or notice | AWAITING COUNSEL |
| Whether, and in what words, a NOTICE names the predecessor | AWAITING COUNSEL (draft wording in `NOTICE`, marked) |
| Written assignment from any individual contributor, including AI-assisted work, to the company | AWAITING COUNSEL |
| Obligations to teams that still use the predecessor | AWAITING COUNSEL (business and legal) |
| CLA or DCO for contributions after launch | AWAITING COUNSEL |

## 6. Nominative use of other parties' names

The full list is `docs/launch/evidence/nominative-use-542a8bf.md`: every mention of Claude Code, Claude, Codex, Cursor, GitHub,
Slack and Anthropic in tracked docs and manifests (non-code files outside `.sdlc/` and `tests/`), as file and line numbers at
commit `542a8bf`, 2,755 term-and-line records in 187 of 310 files (a line naming two terms is recorded twice). Nothing was edited. Where the names sit in the two manifests:
`.claude-plugin/plugin.json:3` (description names GitHub Projects), `:17` (keyword `github-projects`), `:18` (`claude-code`), `:19` (`codex`);
`.claude-plugin/marketplace.json:10` (description names GitHub Projects); `.cursor/rules/output-contract.mdc:14`.

| Question | Answer |
|---|---|
| Are the current references acceptable nominative use? | AWAITING COUNSEL |
| Is a "not affiliated" disclaimer needed, and where? | AWAITING COUNSEL |
| Does a reference to a vendor's plugin catalogue or marketplace need that vendor's terms checked? | AWAITING COUNSEL |

Fact about the tree: a text search of `README.md`, `SUPPORT.md`, `CONTRIBUTING.md`, `SECURITY.md` and `docs/*.md` for "not affiliated",
"unaffiliated", "not endorsed", "no affiliation" and "affiliated with" found no disclaimer sentence on 2026-10-06. This is a phrase
search only; counsel should read the pages, not rely on it.

## 7. Name-collision search, dated 2026-10-06

Run 2026-10-06 from 12:35Z to 13:05Z, by an agent, read-only, over public APIs with no login to any registry other than
the GitHub CLI's own authentication for GitHub REST search. Names searched: `sigmaloop` (the repository and install id) and
`Sigma Loop` (the display name), plus the variants `sigma-loop`, `sigma_loop`, `SigmaLoop`.

THIS IS NOT A TRADEMARK SEARCH. It does not search USPTO, EUIPO, WIPO or any national register, company or business names,
domain names, other social handles, or the Codex and Cursor plugin catalogues (no public query interface was used for either).
It reports what exists, never whether a use is permitted or likely to confuse. That is `AWAITING COUNSEL`.

### 7.1 Method and results

| Registry | Query (REST, read-only) | Result on 2026-10-06 |
|---|---|---|
| GitHub repositories | `search/repositories` with `sigmaloop in:name` | 2 repositories, both 0 stars (hits 1 and 2 below) |
| GitHub repositories | `sigma-loop in:name`, `sigma_loop in:name` | 13 each; the first ten are mostly unrelated (a sigma-delta DAC, web-course repositories, a maths loop exercise); the same-name or close ones are in the table below |
| GitHub repositories | `"sigma loop" in:name,description,readme` | 30; unrelated (a sampler experiment, a trade-model repository, an older radio-streamer bot and similar); none an AI coding tool |
| GitHub repositories | `"sigma loop" claude in:name,description,readme` | 4: one is the owner's own repository, one is hit 1 below, two unrelated |
| GitHub repositories | `"sigma loop" agent in:name,description`; `sigmaloop topic:claude-code` | 0; 0 |
| GitHub repositories | `sigma topic:claude-code-plugin` | 3, all unrelated to a developer-loop plugin (a rules repository, a migration tool, a comparison-matrix tool) |
| GitHub users and organisations | `search/users` for `sigmaloop`, `sigma-loop`; direct `users/` and `orgs/` lookups | `SigmaLoop` is a User (hit 5); `sigmaloopy` is a User (hit 6); `sigma-loop` is an Organization (hit 7); `orgs/sigmaloop` returned 404 because the handle is a user, not an organisation |
| GitHub code | `search/code`: `sigmaloop filename:marketplace.json`; `"sigma loop" filename:plugin.json`; `"sigmaloop" claude plugin` | every hit (1, 1 and 21) is in the owner's own repository; no other repository's plugin manifest matched. Code search indexes a subset of repositories |
| PyPI | `pypi.org/pypi/<name>/json` for `sigmaloop`, `sigma-loop`, `sigma_loop`, `Sigma-Loop`, `SigmaLoop`; and a scan of the full simple index (907,015 project names) for `sigma[-_.]?loop` | all five 404; 0 names in the index match |
| npm | `registry.npmjs.org/<name>` for `sigmaloop`, `sigma-loop`, `sigma_loop`, `@sigmaloop/cli`, `@sigma-loop/cli`; the registry search endpoint for `sigmaloop` and `sigma loop` | all five 404; `sigmaloop` search returned 0; `sigma loop` returned 26,858 loose full-text matches (the top ones are `sigma` 3.0.3, a graph-drawing library, and its plugins): not a usable collision signal |
| VS Code Marketplace | extension query API, criteria text `sigmaloop`, `sigma loop`, `sigma-loop` | `sigmaloop`: 0 extensions; `sigma loop`: 263 total (the text is matched loosely); `sigma-loop`: 185. The first ten of each were read: none is an AI coding agent plugin; they are themes, a signature-format language extension, a smart-board launcher, Sigma Computing's own extension, and some spam listings |
| Claude plugin marketplace (public manifest of the vendor's official catalogue repository, commit `d4226d0`) | read `.claude-plugin/marketplace.json` | 315 plugins; none has `sigma` in its name or description |
| Codex and Cursor catalogues | not queried | no public query interface was used |

### 7.2 The closest hits (ten; URL, date, one line each, no assessment)

| # | URL | Date | What it is |
|---|---|---|---|
| 1 | https://github.com/kohsheen1234/sigmaloop | created and last pushed 2026-04-10 | repository `sigmaloop`, MIT, 0 stars, "Self-improving agent loop: autonomously optimises an agent against a live benchmark through continuo..." |
| 2 | https://github.com/BamiTunes/sigmaLoop | created 2025-07-12 | repository `sigmaLoop`, no description, HTML, 0 stars |
| 3 | https://github.com/jshishimaru/Sigma_Loop | created 2023-12-01 | repository `Sigma_Loop`, no description, C, 0 stars |
| 4 | https://github.com/Peace098/sigma-loop | created 2025-07-05 | repository `sigma-loop`, "Sistema AI per guadagni passivi" (an AI money-making system, in Italian), 0 stars |
| 5 | https://github.com/SigmaLoop | account created 2018-07-18 | a personal GitHub account named `SigmaLoop`, 0 public repositories; the bare handle `sigmaloop` is therefore taken as a user name |
| 6 | https://github.com/sigmaloopy | account created 2025-11-29 | a personal GitHub account, 0 public repositories |
| 7 | https://github.com/sigma-loop | organisation created 2025-12-03 | organisation `sigma-loop`, "An interactive educational platform merging LaTeX mathematics, secure code execution, and RAG-powered AI mentorship", 2 public repositories |
| 8 | https://marketplace.visualstudio.com/items?itemName=SigmaComputing.sigma-vscode-extension | read 2026-10-06 | Sigma Computing's VS Code extension "for managing Sigma data models with git integration" |
| 9 | https://marketplace.visualstudio.com/items?itemName=humpalum.sigma | read 2026-10-06 | "Support for Sigma Signature Format" (an older security rule-format name) |
| 10 | https://www.npmjs.com/package/sigma | version 3.0.3 read 2026-10-06 | the package `sigma`, "A JavaScript library aimed at visualizing graphs"; MIT |

The bare package names `sigmaloop` and `sigma-loop` were not registered on PyPI or npm on 2026-10-06.

### 7.3 Earlier search for the bare word "Sigma" (2026-10-04, summarised, not re-run)

The owner's decision of 2026-10-06 replaced the earlier working name `sigma`, so the generic search was not repeated. Its recorded findings, as
read by the agent on 2026-10-04: 33,974 GitHub repositories with "sigma" in the name; three public repositories whose names are
or begin with `sigma` in the Claude Code plugin space (3, 0 and 0 stars) and Sigma Computing's own Claude Code plugin repositories; the names
`sigma` taken on PyPI (a numerical-methods package) and on npm (the graph library above); 46 VS Code Marketplace matches, none
an AI plugin among the eight read. Because the skill and command family is still `sigma-` prefixed (section 2), counsel may want
that earlier result as well. The "agrim" prefix the issue mentions: no shipped skill directory starts with `agrim`; the string
appears in `LICENSE:3` and in two lines of `skills/sigma-doctor/scripts/doctor.py` (1588 and 1829) that name the repository owner.

| Question | Answer |
|---|---|
| Is `Sigma Loop` acceptable for a developer tool given the hits above and the registers not searched? | AWAITING COUNSEL |
| Does the bare `sigma-` prefix on commands and skills change the answer? | AWAITING COUNSEL |
| Trademark register and company-name search (USPTO, EUIPO at minimum), domains, handles | AWAITING COUNSEL (not done) |
| Is the `sigma-loop` organisation or the `SigmaLoop` user a concern for the repository or install id `sigmaloop`? | AWAITING COUNSEL |

## 8. Everything still awaiting counsel

1. Copyright holder (company or individual) and any assignment, including AI-assisted work (section 3 and 5).
2. Provenance: the predecessor's ownership and licence, relicensing consent or notice, and the NOTICE wording (section 5; draft in `NOTICE`).
3. The name `Sigma Loop` and the id `sigmaloop`, with the unsearched registers (section 7).
4. Nominative use and any disclaimer (section 6).
5. Whether the third-party notices in `NOTICE` are what is required for the optional and test-only dependencies (section 4).
6. Obligations to teams still using the predecessor; privacy text approval; a CLA or DCO.

## 9. Decision line

Counsel and owner decision on the name and the notice: AWAITING COUNSEL

D13 stays unscoreable until that line is filled in by the people who decide.
