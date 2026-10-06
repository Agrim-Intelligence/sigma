# Nominative-use scan: third-party names in shipped docs and manifests

Fact sheet input for counsel, issue #343. This is a list of where names appear. It says nothing about
whether any use is permitted, whether a disclaimer is needed, or whether any wording should change: that is
`AWAITING COUNSEL` (see `legal.md`, section 6). Nothing in the tree was edited as part of this scan.

- Tree scanned: commit `542a8bf6c33736f6071de4c6f7a8bc710ed3d84f` (base of the pull request that added this file). Line numbers are valid for that
  commit and go stale as the files change; re-run the command below to refresh.
- Scanned: 310 tracked non-code files, of which 187 contain a match and are listed below. Scope: tracked files outside `.sdlc/` and `tests/`,
  excluding `*.py`, `*.sh` and image files (code comments and docstrings are NOT in this list; ask for them if wanted).
- Terms, matched case-insensitively: `Claude Code` (space or hyphen), `Claude` not followed by `Code`, `Codex`, `Cursor`,
  `GitHub`, `Slack`, `Anthropic`. Case-insensitive on purpose: a lowercase or hyphenated hit (`claude-code`, `.claude/`,
  `github.com` URLs, a plugin id, `cursor` as a pagination or database word) is listed separately as "other form" so counsel can
  see identifiers and URLs too. Many "other form" hits for `Cursor` and `Claude` are paths, ids or ordinary words.
- Method: for each tracked file, each line is tested against the case-insensitive pattern for each term; a line is recorded
  once per term. Equivalent to
  `git grep -n -I -i -E "Claude[ -]Code|Claude|Codex|Cursor|GitHub|Slack|Anthropic" -- . ':!.sdlc' ':!tests' ':!*.py' ':!*.sh'`
  grouped by file and term (the plain `Claude` alternative there also matches the `Claude Code` lines; the list separates them).
  A generated list, not a hand-reviewed one.
- How to read: under a file heading, `Term: 12, 40` means `<file>:12` and `<file>:40`. "exact" means the capitalised
  brand form; "other form" means any other casing or hyphenation.

Totals (lines recorded, all files): Claude Code: 138; Claude (not followed by Code): 695; Codex: 385; Cursor: 256; GitHub: 687; Slack: 484; Anthropic: 110.


## manifests and host rule files


### `.claude-plugin/marketplace.json`

- GitHub - exact: 10

### `.claude-plugin/plugin.json`

- Claude Code - other form: 18
- Codex - other form: 19
- GitHub - exact: 3; other form: 17

### `.cursor/rules/output-contract.mdc`

- Cursor - exact: 14

## repository templates and workflows


### `.github/ISSUE_TEMPLATE/bug_report.yml`

- Claude Code - exact: 17
- Codex - exact: 17
- Cursor - exact: 17

### `.github/ISSUE_TEMPLATE/config.yml`

- GitHub - exact: 5; other form: 4

### `.github/workflows/ci.yml`

- GitHub - other form: 10, 39

### `.github/workflows/flake-census.yml`

- GitHub - other form: 27

## top-level documents


### `.gitignore`

- Claude (not followed by Code) - other form: 30

### `AGENTS.md`

- Claude Code - exact: 47, 48
- Claude (not followed by Code) - other form: 3
- Codex - exact: 47
- Cursor - exact: 47, 48; other form: 21

### `CHANGELOG.md`

- Claude Code - exact: 392, 475, 998, 1053, 1135, 1146, 1188, 1219, 1237, 1290, 1367
- Claude (not followed by Code) - exact: 897; other form: 46, 164, 169, 590, 611, 615, 816, 1134, 1145, 1146, 1167, 1188, 1216
- Codex - exact: 46, 58, 78, 478, 820, 885, 887, 889, 897, 998, 1053, 1135, 1147, 1168, 1189, 1221, 1237, 1290, 1323, 1367, 1368; other form: 79, 164, 610, 615, 816, 886, 1283
- Cursor - exact: 77, 478, 589, 611, 820, 897, 998, 1053, 1135, 1237, 1290, 1369; other form: 23, 77, 78, 232, 233
- GitHub - exact: 55, 66, 153, 157, 250, 480, 646, 666, 668, 710, 731, 741, 778, 800, 820, 821, 869, 994, 1035, 1046, 1049, 1060, 1072, 1089, 1256, 1258, 1267, 1269, 1335, 1349, 1358; other form: 47, 151, 476, 483, 494, 625, 631, 642, 735, 737, 761, 762, 771, 773, 776, 806, 994, 995, 1013, 1018, 1019, 1028, 1032, 1045, 1051, 1052, 1138, 1139, 1257, 1262, 1263, 1264, 1265, 1271, 1335, 1336
- Slack - exact: 139, 140, 264, 480, 1317; other form: 91
- Anthropic - exact: 111

### `CODE_OF_CONDUCT.md`

- GitHub - other form: 81

### `README.md`

- Claude Code - exact: 158, 164, 168, 170, 278, 363, 367, 373, 374, 402, 458, 1952, 1991, 2030, 2039, 2083, 2159, 2216
- Claude (not followed by Code) - exact: 79, 85, 123, 163, 170, 410, 443, 476, 643, 727, 1639, 1717, 2006, 2019, 2098, 2127, 2235, 2238, 2241, 2252, 2265; other form: 29, 33, 38, 39, 180, 181, 198, 1646, 1649, 1722, 1988, 2025, 2037, 2038, 2098, 2253, 2273, 2275
- Codex - exact: 80, 82, 159, 164, 165, 191, 198, 201, 232, 367, 373, 402, 410, 415, 476, 727, 1716, 1717, 1952, 2120, 2159, 2160, 2161, 2216, 2217, 2243, 2245, 2252, 2253, 2255, 2256, 2258, 2262, 2264, 2266; other form: 80, 194, 195, 199, 202, 203, 206, 243, 367, 415, 452, 476, 729, 2122, 2245, 2248, 2255, 2257
- Cursor - exact: 159, 209, 211, 212, 232, 367, 373, 402, 414, 1952, 2020, 2162, 2217, 2218, 2220, 2222, 2223, 2224, 2226, 2227, 2234, 2235, 2239; other form: 213, 217, 243, 367, 414, 1529, 1579, 1580, 2231, 2234, 2235
- GitHub - exact: 21, 64, 239, 240, 297, 310, 388, 389, 391, 393, 641, 682, 733, 750, 752, 766, 847, 940, 953, 970, 991, 997, 999, 1039, 1041, 1042, 1046, 1056, 1115, 1164, 1168, 1172, 1222, 1242, 1267, 1284, 1569, 1583, 1586, 1782, 1810, 1811, 1822, 1829, 1833, 1863, 1871, 1876, 1879, 1887, 1901, 2189, 2197, 2211; other form: 7, 64, 161, 173, 180, 194, 216, 227, 240, 248, 252, 253, 257, 292, 293, 294, 295, 311, 314, 371, 396, 416, 467, 756, 757, 780, 989, 997, 1003, 1008, 1031, 1044, 1056, 1057, 1066, 1112, 1113, 1417, 1423, 1577, 1879, 2189, 2272, 2273
- Anthropic - exact: 1722, 2273; other form: 1955, 2273

### `SECURITY.md`

- GitHub - exact: 5, 12

### `SUPPORT.md`

- GitHub - exact: 3

## docs/


### `docs/agent-rules-detail.md`

- Claude Code - exact: 14
- Claude (not followed by Code) - other form: 10, 13, 14, 15, 17
- GitHub - exact: 152

### `docs/bench/preregistration.md`

- Claude Code - exact: 13, 14, 15
- Claude (not followed by Code) - other form: 19, 378

### `docs/bench/task-sourcing.md`

- Claude (not followed by Code) - exact: 18; other form: 18, 20, 158
- GitHub - exact: 51, 133; other form: 58, 59, 60, 61, 62, 63, 64, 65
- Anthropic - exact: 18

### `docs/board-fields.md`

- GitHub - exact: 71, 113, 118, 119, 122, 124, 140, 192, 197, 274; other form: 137, 151, 203, 228, 234, 251

### `docs/board.md`

- GitHub - exact: 37, 61, 64, 85, 119, 123, 139; other form: 146, 201

### `docs/branching-model.md`

- Claude (not followed by Code) - other form: 1597, 1598
- Cursor - other form: 1565, 1598
- GitHub - exact: 121, 392, 546, 553, 663, 683, 1202, 1225, 1362, 1390, 1411, 1413, 1434, 1519, 1852, 1953, 2037, 2064, 2143; other form: 540, 548, 603, 607, 1300
- Slack - exact: 236, 1376

### `docs/dossier-pipeline.md`

- Claude Code - exact: 113, 1099
- Claude (not followed by Code) - other form: 184
- Cursor - exact: 113
- GitHub - exact: 448, 533, 1136
- Anthropic - exact: 385, 1183

### `docs/enforcement.md`

- Claude Code - exact: 10, 23, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62
- Claude (not followed by Code) - exact: 39
- Codex - exact: 11, 24, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 58, 59, 60, 62, 83; other form: 39, 60
- Cursor - exact: 11, 24, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 58, 59, 60, 62, 83; other form: 60
- GitHub - exact: 30, 36, 50, 51, 52

### `docs/executor-parity/brainstorm.md`

- Claude (not followed by Code) - exact: 3, 16, 20, 21

### `docs/executor-parity/implement.md`

- Claude (not followed by Code) - exact: 4, 34

### `docs/executor-parity/plan.md`

- Claude (not followed by Code) - exact: 4

### `docs/executor-parity/review.md`

- Claude (not followed by Code) - exact: 4, 49; other form: 20, 34
- Cursor - exact: 29, 50

### `docs/executor-parity/verify.md`

- Claude (not followed by Code) - exact: 3, 4, 20

### `docs/how-the-dossier-pipeline-works.md`

- GitHub - exact: 107

### `docs/label-model.md`

- Codex - exact: 1198
- GitHub - exact: 11, 177, 611, 779, 1143, 1186

### `docs/launch/b6-claims-sessions-locks.md`

- Slack - exact: 8, 144; other form: 25, 44, 49, 117

### `docs/launch/b6-committed-work-records.md`

- GitHub - other form: 121

### `docs/launch/b6-knowledge-and-records.md`

- GitHub - exact: 76, 148

### `docs/launch/b6-per-goal-state.md`

- Slack - other form: 36, 130

### `docs/launch/b6-singletons-and-home.md`

- Codex - other form: 116
- Cursor - other form: 24, 43, 48, 68, 69, 70, 71, 96, 107, 108, 109, 110, 115, 138, 143, 157, 159, 162, 167, 226, 230, 240
- Slack - other form: 46, 47

### `docs/launch/b6-slack-supervisor-logs.md`

- Claude (not followed by Code) - other form: 17, 31
- Codex - other form: 17
- Cursor - other form: 17
- Slack - exact: 1, 30; other form: 10, 14, 21, 24, 59, 68, 71

### `docs/launch/b6-worktrees.md`

- Cursor - exact: 70
- GitHub - exact: 14

### `docs/launch/blast-radius.md`

- GitHub - exact: 22, 29

### `docs/launch/coverage.json`

- GitHub - exact: 44, 47

### `docs/launch/coverage.md`

- GitHub - exact: 60
- Slack - other form: 34, 65

### `docs/launch/decision-rule.md`

- GitHub - exact: 19, 25, 89, 145; other form: 174, 175

### `docs/launch/definition.json`

- Claude Code - other form: 11, 12, 14
- Codex - other form: 15
- Cursor - other form: 15
- GitHub - other form: 9, 11, 12
- Slack - other form: 16

### `docs/launch/definition.md`

- Claude Code - exact: 27; other form: 118, 119
- Claude (not followed by Code) - other form: 29, 30, 75, 77
- Codex - exact: 108, 109, 110; other form: 120
- Cursor - other form: 121
- GitHub - exact: 44, 77, 88; other form: 41, 50, 52, 64, 78, 87, 96, 111, 118, 119
- Slack - exact: 132; other form: 132

### `docs/launch/dispositions/458.json`

- Slack - other form: 19, 20, 21, 26, 27, 28

### `docs/launch/dispositions/460.json`

- Slack - other form: 3, 6, 7, 14, 21, 28, 31, 34, 35

### `docs/launch/dispositions/463.json`

- GitHub - exact: 98, 105

### `docs/launch/dispositions/464.json`

- Slack - other form: 38, 122, 125, 126

### `docs/launch/dispositions/466.json`

- Codex - other form: 41
- Cursor - other form: 45, 49, 59, 63, 73, 76, 77, 104
- Slack - exact: 160; other form: 157, 161, 164, 168, 171, 174, 175

### `docs/launch/evidence/330-control.md`

- Codex - other form: 68
- Cursor - other form: 83
- GitHub - other form: 94, 107
- Slack - other form: 79

### `docs/launch/evidence/429-controls.md`

- GitHub - exact: 39; other form: 103, 148, 154, 177, 183, 196, 201, 207, 328, 380, 390, 407

### `docs/launch/evidence/ci-supported-cells-bd969b48018a.json`

- GitHub - other form: 6, 28

### `docs/launch/evidence/cost-calibration.md`

- Claude (not followed by Code) - other form: 68, 79
- Anthropic - other form: 6

### `docs/launch/evidence/drills-cde9869d0cf8.json`

- Claude Code - exact: 1093, 1142, 1191, 1240, 1289
- Claude (not followed by Code) - other form: 1082, 1093, 1131, 1142, 1180, 1191, 1229, 1240, 1278, 1289
- Codex - exact: 1093, 1142, 1191, 1240, 1289; other form: 1093, 1142, 1191, 1240, 1289
- Cursor - other form: 560, 584, 627, 651, 694, 718, 761, 785, 828, 852
- GitHub - exact: 5; other form: 3, 1093, 1142, 1191, 1240, 1289
- Slack - exact: 1093, 1142, 1191, 1240, 1289; other form: 1093, 1142, 1191, 1240, 1289

### `docs/launch/evidence/egress-dc79750ab8d6.json`

- Codex - other form: 17
- GitHub - other form: 10, 11

### `docs/launch/evidence/egress-doctor-local.json`

- Codex - other form: 3

### `docs/launch/evidence/egress-full-suite-a9747324eee1.json`

- Claude (not followed by Code) - other form: 3
- Codex - other form: 7
- Cursor - other form: 11

### `docs/launch/evidence/egress-full-suite.json`

- Claude (not followed by Code) - other form: 3
- Codex - other form: 7
- Cursor - other form: 11

### `docs/launch/evidence/exposure-8aee0c74c526.json`

- GitHub - exact: 9131; other form: 8194, 9131
- Slack - other form: 5658, 5660, 5662, 5663

### `docs/launch/evidence/exposure-8aee0c74c526.md`

- Slack - other form: 69

### `docs/launch/evidence/flake-c82e3dfa7420.json`

- Claude (not followed by Code) - other form: 105
- Codex - other form: 106, 216
- GitHub - other form: 14

### `docs/launch/evidence/mechanical-gates-859290305d97.json`

- GitHub - exact: 471
- Slack - other form: 69, 667, 691, 978, 993
- Anthropic - other form: 1269

### `docs/launch/evidence/mechanical-gates-859290305d97.md`

- GitHub - exact: 61
- Slack - exact: 68

### `docs/launch/evidence/meter-sonnet-5-5-cf31b72c6967.json`

- Claude (not followed by Code) - other form: 18, 28, 36, 44, 52, 60, 68, 76
- Anthropic - other form: 13

### `docs/launch/evidence/mutation-70c2c6e96136.json`

- Claude Code - exact: 1647
- Claude (not followed by Code) - other form: 89, 93, 553
- Codex - exact: 1072; other form: 2159, 2163, 2179, 2183, 2243, 2247, 2251, 2255, 2259, 2275, 2279
- Cursor - exact: 1072; other form: 1458, 1462, 1482
- GitHub - other form: 863, 883, 887, 891, 955, 1255
- Slack - other form: 1806, 1872, 1878, 1897, 1901, 1905, 1909, 1913, 1917, 1921, 1925, 1929, 1933, 1937, 1941, 1945, 1949, 1953, 1957, 1961, 1965, 1969, 1973, 1977, 1981, 1985, 1989, 1993, 1997, 2001, 2005, 2009, 2013, 2017, 2021, 2025, 2029, 2033, 2037, 2041, 2045, 2055, 2061, 2706, 2724

### `docs/launch/evidence/pin-rollback-2026-10-02.md`

- Claude Code - exact: 7
- Claude (not followed by Code) - other form: 8, 27, 39, 56
- Codex - other form: 8, 27, 39
- GitHub - exact: 14, 29, 41; other form: 32, 44, 51

### `docs/launch/evidence/refs-8aee0c74c526.json`

- Claude (not followed by Code) - other form: 13

### `docs/launch/evidence/review-s4-c3faf6f23e12.json`

- Slack - exact: 7

### `docs/launch/evidence/review-units-a5c615062313.json`

- Slack - other form: 188, 199

### `docs/launch/evidence/shared-sdlc-paths-4c8562f.json`

- Cursor - other form: 48, 52, 57, 832, 833, 837, 891, 892, 896, 929, 932, 944, 947, 967, 968, 970, 982, 983, 985, 995, 1081, 1199, 3036, 3285, 3286, 3290, 3311, 3314, 3322, 3323, 3325, 3330, 3345, 3378
- Slack - other form: 56, 89, 90, 91, 825, 826, 827, 828, 829, 830, 831, 885, 886, 887, 888, 889, 890, 1020, 1030, 1046, 1056, 1107, 1118, 1154, 1164, 1172, 1181, 1191, 1407, 1751, 1764, 1786, 1799, 1827, 1847, 1874, 1893, 1923, 1945, 2010, 2014, 2015, 2016, 2017, 2022, 2023, 2024, 2025, 2032, 2036, 2037, 2038, 2039, 2044, 2045, 2046, 2047, 2054, 2058, 2059, 2064, 2065, 3279, 3280, 3281, 3282, 3283, 3284, 3335, 3339, 3355, 3369, 3370, 3537, 3550, 3563, 3582, 3601, 3612, 3613, 3614, 3615, 3616, 3618, 3619, 3620, 3621, 3622, 3624, 3625, 3626

### `docs/launch/evidence/status-a5c615062313.json`

- Claude (not followed by Code) - other form: 1
- Codex - exact: 1
- Cursor - exact: 1
- GitHub - exact: 1; other form: 1

### `docs/launch/evidence/tracked-8aee0c74c526.json`

- Slack - other form: 881, 883, 885, 886

### `docs/launch/evidence/tracked-8aee0c74c526.md`

- Slack - other form: 69

### `docs/launch/exposure-allowlist.json`

- GitHub - other form: 61

### `docs/launch/flake-census.md`

- GitHub - exact: 53

### `docs/launch/growth-audit.json`

- Cursor - other form: 83, 89, 96, 2047, 2087, 2177, 4604, 4658, 4721
- Slack - other form: 94, 150, 151, 152, 153, 154, 155, 299, 304, 349, 354, 769, 779, 784, 1699, 1704, 1709, 1714, 1719, 1724, 1729, 1734, 1739, 1744, 1749, 1754, 1759, 1764, 1899, 1904, 2069, 2074, 2114, 2167, 2169, 2839, 2939, 3024, 3139, 3264, 3307, 3309, 3312, 3314, 3317, 3319, 3322, 3324, 3327, 3329, 3332, 3334, 3337, 3339, 3342, 3344, 3347, 3349, 3352, 3354, 3357, 3359, 3362, 3364, 3367, 3369, 3372, 3374, 3377, 3379, 3382, 3384, 3387, 3389, 3392, 3394, 3397, 3399, 3402, 3404, 3407, 3409, 3412, 3414, 3417, 3419, 3422, 3424, 3427, 3429, 3946, 4054, 4063, 4567, 4576, 4630, 4639, 4666, 4702, 4703, 5125, 5143, 5152, 5161, 5179, 5206, 5207, 5215, 5216, 5224, 5225, 5233, 5234, 5242, 5243, 5251, 5252

### `docs/launch/growth-audit.md`

- Cursor - other form: 71
- GitHub - exact: 43; other form: 41
- Slack - exact: 71; other form: 71

### `docs/launch/repo-settings.md`

- GitHub - exact: 12, 24, 25, 35, 36, 38, 40, 43, 46, 50, 60, 65, 78, 130, 154, 165, 183; other form: 43, 57, 65, 167, 168, 169, 170, 171

### `docs/launch/retention-event-time-ledger.md`

- Claude (not followed by Code) - other form: 82

### `docs/launch/retention-review-evidence.md`

- GitHub - exact: 20; other form: 69

### `docs/launch/review-plan.md`

- Claude Code - exact: 23, 24, 81
- Codex - exact: 26, 100, 197
- Cursor - exact: 26, 197
- GitHub - exact: 24, 81; other form: 25, 81, 86, 195, 198, 203

### `docs/launch/review-units.json`

- Slack - other form: 177, 188

### `docs/launch/seeded-defects.md`

- Claude Code - exact: 147

### `docs/launch/shared-sdlc-paths.md`

- Cursor - other form: 149, 153, 158
- GitHub - exact: 39
- Slack - other form: 150, 151, 154, 156, 157, 169, 183, 184, 185, 186, 187, 190, 191, 192

### `docs/launch/write-surface.json`

- Claude (not followed by Code) - other form: 64
- Codex - other form: 213
- Cursor - other form: 221, 349, 389, 461, 1405, 1413, 1717
- GitHub - exact: 2552; other form: 237, 1184, 1192, 1200, 1208, 1216, 1224, 1232, 1240, 1248, 1256, 1264, 1272, 1280, 1288, 1296, 1304, 1312, 1320, 1328, 1336, 1344, 1352, 1360, 1368, 1376, 1384, 1392, 1400, 2397, 2405
- Slack - other form: 1124, 1132, 1140, 1148, 1156, 1164, 1172

### `docs/launch/write-surface.md`

- Claude (not followed by Code) - other form: 14
- Codex - other form: 33
- Cursor - other form: 34, 51, 56, 65, 183, 184, 222
- GitHub - exact: 325; other form: 37, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179, 180, 181, 182, 306, 307
- Slack - other form: 148, 149, 150, 151, 152, 153, 154

### `docs/onboarding-control.md`

- Claude Code - exact: 25, 44, 48, 156, 171, 172, 263, 272, 289
- Claude (not followed by Code) - other form: 20, 21, 22, 23, 40, 165, 166, 172, 173, 174, 175, 245, 250
- Codex - exact: 176, 178, 179, 274; other form: 20, 21, 23, 41, 167, 168, 179, 245
- Cursor - exact: 275; other form: 275
- GitHub - exact: 69, 273, 278; other form: 29, 30, 40, 41, 42, 46, 62, 67, 69, 70, 80, 94, 96, 112, 121, 126, 127, 128, 133, 135, 137, 139, 142, 143, 147, 148, 150, 198, 240, 241, 251, 254, 273, 290, 291

### `docs/output-contract-detail.md`

- Claude (not followed by Code) - exact: 27, 28; other form: 15, 57
- Codex - exact: 28, 76

### `docs/output-contract.md`

- Claude Code - exact: 3
- Claude (not followed by Code) - other form: 185, 311
- Codex - exact: 3
- Cursor - exact: 3

### `docs/ownership.md`

- GitHub - other form: 19, 68, 87

### `docs/privacy.md`

- Claude (not followed by Code) - other form: 19
- Codex - other form: 19
- GitHub - exact: 5, 9, 10
- Slack - exact: 11; other form: 11

### `docs/public-snapshot.md`

- Claude (not followed by Code) - other form: 275
- GitHub - exact: 116, 151, 285; other form: 254, 306

### `docs/publish-runbook.md`

- GitHub - exact: 38, 54, 63, 155, 163, 184

### `docs/release.md`

- Claude (not followed by Code) - other form: 9, 10, 45, 46, 48
- GitHub - exact: 4, 11, 61, 80; other form: 59

### `docs/threat-model.md`

- GitHub - exact: 3, 12, 25, 33, 58
- Slack - exact: 25, 26, 42, 48, 53; other form: 13, 22, 26, 48, 53

### `docs/uninstall.md`

- Claude (not followed by Code) - other form: 14
- Codex - exact: 16; other form: 16, 26, 27
- Cursor - exact: 27; other form: 29
- GitHub - exact: 43
- Slack - other form: 8

### `docs/upgrading.md`

- Claude Code - exact: 15, 22, 306, 324, 352, 436; other form: 441
- Claude (not followed by Code) - exact: 440, 442; other form: 28, 29, 30, 31, 36, 299, 306, 308, 324, 326, 438
- Codex - exact: 16, 40, 50, 52, 149, 311, 312, 329, 330, 445, 462; other form: 43, 44, 45, 48, 53, 131, 312, 447
- Cursor - exact: 52, 54, 352, 462; other form: 52, 53
- GitHub - exact: 105, 135; other form: 36

## skills/ (non-code files)


### `skills/sigma-align/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 50, 136
- Codex - exact: 7, 9
- GitHub - exact: 37; other form: 36

### `skills/sigma-audit/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 34
- Codex - exact: 7, 9
- GitHub - exact: 86

### `skills/sigma-brainstorm/SKILL.md`

- Claude Code - exact: 17
- Claude (not followed by Code) - exact: 10; other form: 8, 38, 55
- Codex - exact: 6, 8
- Cursor - exact: 19
- GitHub - exact: 57

### `skills/sigma-context/SKILL.md`

- Claude (not followed by Code) - exact: 11, 53; other form: 9, 22, 35, 42, 47
- Codex - exact: 7, 9
- GitHub - other form: 39

### `skills/sigma-contract-check/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 24, 30
- Codex - exact: 7, 9

### `skills/sigma-debug/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 22, 24
- Codex - exact: 7, 9

### `skills/sigma-decide/SKILL.md`

- Claude (not followed by Code) - other form: 46, 93, 94

### `skills/sigma-define/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 49, 73, 87, 99, 117, 138, 164
- Codex - exact: 7, 9
- GitHub - exact: 193, 206

### `skills/sigma-doctor/SKILL.md`

- Claude (not followed by Code) - exact: 11, 105, 108, 198, 272; other form: 9, 22, 55, 65, 73, 74, 110, 112, 126, 150
- Codex - exact: 7, 9, 102, 104, 105, 198, 272; other form: 102
- Cursor - exact: 188; other form: 263, 274
- GitHub - exact: 34; other form: 30

### `skills/sigma-doctor/references/pre-launch-id.md`

- GitHub - exact: 8

### `skills/sigma-dossier/SKILL.md`

- Claude Code - exact: 29, 30
- Claude (not followed by Code) - exact: 11; other form: 9, 40, 46, 101
- Codex - exact: 7, 9
- Cursor - exact: 29

### `skills/sigma-goal-design/references/mapping-the-codebase.md`

- Claude (not followed by Code) - other form: 53, 147
- Anthropic - exact: 164

### `skills/sigma-goal-design/references/writing-the-artifact.md`

- GitHub - exact: 135, 273

### `skills/sigma-goal-review/SKILL.md`

- Claude (not followed by Code) - other form: 56, 57, 58, 59, 71, 80

### `skills/sigma-goal-review/references/adjudication.md`

- Claude (not followed by Code) - other form: 20, 29, 30, 44

### `skills/sigma-goal-review/references/confirm.md`

- Claude (not followed by Code) - other form: 13, 134, 157, 214, 231

### `skills/sigma-goal-review/references/feature-ification.md`

- Claude (not followed by Code) - other form: 31, 45, 78, 94, 113, 130, 136, 143

### `skills/sigma-goal-review/references/reading-the-target.md`

- Claude (not followed by Code) - other form: 64

### `skills/sigma-goal/SKILL.md`

- Claude (not followed by Code) - exact: 11, 49, 57, 97, 99; other form: 9, 25, 36, 41, 49, 63, 73, 76, 80, 86, 91, 121, 132, 183
- Codex - exact: 7, 9, 49, 94, 100, 102, 105, 106; other form: 49, 104
- GitHub - exact: 37, 45, 66; other form: 74, 78

### `skills/sigma-implement/SKILL.md`

- Claude Code - exact: 27, 38
- Claude (not followed by Code) - exact: 11; other form: 9, 73, 115
- Codex - exact: 7, 9
- Cursor - exact: 30
- GitHub - other form: 133

### `skills/sigma-init/SKILL.md`

- Claude Code - exact: 64, 105, 117, 154, 179
- Claude (not followed by Code) - exact: 11; other form: 9, 23, 106, 120, 135, 170, 181, 185, 189
- Codex - exact: 7, 9, 25, 67, 105, 122, 157, 191; other form: 25, 46, 86, 167
- Cursor - exact: 26, 67, 105, 122, 157, 191; other form: 27, 46, 86, 167
- GitHub - exact: 29, 40, 109, 114; other form: 29, 30, 40, 43, 44, 47, 48, 76, 87, 111, 124, 125, 127, 167

### `skills/sigma-init/github-templates/ISSUE_TEMPLATE/epic.md.tmpl`

- GitHub - exact: 15

### `skills/sigma-init/github-templates/workflows/add-to-project.yml.tmpl`

- GitHub - other form: 20

### `skills/sigma-init/references/board.md`

- GitHub - exact: 1, 14, 89; other form: 6, 94

### `skills/sigma-init/templates/config.json.tmpl`

- Claude (not followed by Code) - exact: 61, 63, 107, 181, 191; other form: 119, 180, 181
- Codex - exact: 61, 63, 107, 181, 185, 191; other form: 60, 61, 181, 191
- Cursor - exact: 181; other form: 156, 181
- GitHub - exact: 8, 41, 93, 95, 96, 101, 104, 108; other form: 6, 14, 15, 17, 20, 38, 84, 86, 112, 116, 119, 154, 170
- Slack - exact: 156, 158; other form: 155, 156, 157, 158
- Anthropic - exact: 176

### `skills/sigma-kg/SKILL.md`

- Claude Code - exact: 105
- Claude (not followed by Code) - exact: 11; other form: 9, 27, 30, 43, 46, 72, 112, 142, 180
- Codex - exact: 7, 9, 106
- Cursor - exact: 106
- Anthropic - other form: 94

### `skills/sigma-ledger/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 34, 49
- Codex - exact: 7, 9

### `skills/sigma-log/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 25, 33, 37
- Codex - exact: 7, 9

### `skills/sigma-loop/AUTOWATCH.md`

- Claude Code - exact: 39, 82
- Claude (not followed by Code) - exact: 39, 56, 61, 140, 144, 148, 175, 179; other form: 82, 108, 120, 124
- Cursor - other form: 194, 195
- GitHub - exact: 143
- Slack - exact: 165, 175, 193, 200; other form: 207
- Anthropic - exact: 86

### `skills/sigma-loop/SKILL.md`

- Claude (not followed by Code) - exact: 11, 131, 168; other form: 9, 20, 30, 36, 43, 46, 69, 70, 71, 85, 124, 129, 131, 143, 147, 159, 163, 164, 180, 186
- Codex - exact: 7, 9, 42, 131, 142, 145, 147, 169; other form: 142, 144, 148, 170
- GitHub - exact: 18, 95; other form: 18, 82, 96, 180, 248
- Slack - exact: 256; other form: 256, 257

### `skills/sigma-loop/SLACK_COMMANDS.md`

- Slack - exact: 1, 3, 4, 25, 35, 45, 52, 60, 75, 139, 343; other form: 3, 27, 28, 37, 45, 54, 61, 63, 79, 95, 100, 101, 104, 105, 111, 115, 121, 124, 125, 150, 155, 156, 190, 203, 204, 211, 222, 228, 229, 234, 238, 239, 244, 248, 254, 257, 295, 304, 308, 314, 315, 321, 325, 332, 333, 336, 339, 348, 358, 363

### `skills/sigma-loop/channels/sigma-autowatch/README.md`

- Claude Code - exact: 29, 41
- Claude (not followed by Code) - exact: 3; other form: 4, 5, 14, 55
- Anthropic - exact: 15, 24

### `skills/sigma-loop/channels/sigma-autowatch/package.json`

- Claude Code - exact: 5

### `skills/sigma-loop/channels/sigma-autowatch/webhook.ts`

- Claude Code - exact: 11, 27, 42
- Claude (not followed by Code) - other form: 5, 28, 78
- Cursor - other form: 69
- Anthropic - exact: 12

### `skills/sigma-loop/rates/anthropic_list_prices.csv`

- Claude (not followed by Code) - other form: 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60
- Anthropic - exact: 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 17, 18, 22, 23, 24, 28, 29, 30, 40, 41, 42, 44, 45; other form: 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60

### `skills/sigma-loop/references/filing.md`

- Claude (not followed by Code) - other form: 13, 26, 90
- GitHub - other form: 93, 95

### `skills/sigma-loop/references/landing.md`

- Claude (not followed by Code) - exact: 52; other form: 42, 45, 46, 60, 95, 129, 132, 135, 138, 140, 141, 144, 146, 176, 182, 282, 359, 365
- GitHub - exact: 160, 182, 187, 206, 210, 212, 219, 238, 245, 253, 261, 294, 315

### `skills/sigma-loop/references/picking.md`

- Claude (not followed by Code) - exact: 81; other form: 15, 34, 82, 84, 94, 96, 136, 140, 142, 163, 210, 220, 221, 236, 252
- Codex - exact: 22, 24, 29, 76, 82, 101; other form: 23, 82
- Cursor - other form: 16
- GitHub - exact: 12, 148, 153, 228; other form: 12, 72, 186, 230

### `skills/sigma-loop/references/progress.md`

- Claude (not followed by Code) - exact: 28, 30, 31, 58, 60, 70; other form: 9, 11, 24, 36, 45, 52, 83, 101, 113, 117, 118, 123
- Codex - exact: 16, 56, 62, 67, 77; other form: 52, 64, 65, 69, 78
- GitHub - other form: 88, 107, 108, 109, 110, 113
- Anthropic - exact: 68

### `skills/sigma-loop/references/running.md`

- Claude (not followed by Code) - exact: 53, 58, 121, 240, 248, 296, 365; other form: 44, 62, 72, 102, 108, 112, 126, 141, 148, 161, 174, 176, 178, 179, 182, 184, 220, 232, 274, 291, 305, 340, 342, 353, 355, 363
- Codex - exact: 58, 61, 63, 64, 65, 69, 99, 111, 113, 240, 248, 252, 295, 340, 341, 343, 345, 350, 365; other form: 62, 66, 112, 245, 249, 340, 342, 363
- Cursor - exact: 249
- GitHub - exact: 122, 129, 313; other form: 26, 35, 46, 123, 274, 313
- Anthropic - exact: 64

### `skills/sigma-loop/references/selection.md`

- GitHub - exact: 3

### `skills/sigma-loop/references/stopping.md`

- Claude (not followed by Code) - exact: 40; other form: 14
- Codex - exact: 40
- GitHub - other form: 23

### `skills/sigma-migration-check/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 24, 25
- Codex - exact: 7, 9

### `skills/sigma-model/SKILL.md`

- Claude (not followed by Code) - exact: 11, 17, 31; other form: 9, 21, 40, 75, 105, 113, 135, 182
- Codex - exact: 7, 9, 18, 24, 27, 29, 93, 94, 165, 175; other form: 21, 29
- GitHub - exact: 108

### `skills/sigma-plan-review/SKILL.md`

- Claude (not followed by Code) - exact: 10, 191; other form: 8, 30, 51, 53, 61, 95, 125, 180, 185
- Codex - exact: 6, 8
- GitHub - other form: 186

### `skills/sigma-plan/SKILL.md`

- Claude Code - exact: 20, 63, 67
- Claude (not followed by Code) - exact: 61
- Codex - exact: 63
- Cursor - exact: 21, 63

### `skills/sigma-promote/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 26, 30, 58, 59
- Codex - exact: 7, 9
- GitHub - exact: 21, 93; other form: 50

### `skills/sigma-radar/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 23, 47
- Codex - exact: 7, 9
- Cursor - other form: 23, 24
- GitHub - exact: 19, 34; other form: 22

### `skills/sigma-rebase/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 40, 55, 70, 90
- Codex - exact: 7, 9

### `skills/sigma-research/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 99, 173
- Codex - exact: 7, 9
- GitHub - exact: 168; other form: 110, 175, 179

### `skills/sigma-retro/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 47, 49, 87, 94, 107, 123
- Codex - exact: 7, 9
- GitHub - other form: 42, 44, 88, 124

### `skills/sigma-review/SKILL.md`

- Claude Code - exact: 74
- Claude (not followed by Code) - exact: 11; other form: 9, 29, 50, 52, 60, 137, 141, 144, 170
- Codex - exact: 7, 9
- Cursor - exact: 3, 76, 83

### `skills/sigma-review/references/axes.md`

- GitHub - other form: 11

### `skills/sigma-review/references/selection.md`

- Cursor - exact: 3

### `skills/sigma-scope/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 28, 44, 86, 96, 105, 179, 184, 189, 193, 194
- Codex - exact: 7, 9
- GitHub - exact: 3, 84

### `skills/sigma-scope/references/selection.md`

- GitHub - exact: 3

### `skills/sigma-security-review/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 24, 29
- Codex - exact: 7, 9

### `skills/sigma-setup/SKILL.md`

- Claude Code - exact: 31
- Claude (not followed by Code) - exact: 11; other form: 9, 21
- Codex - exact: 7, 9, 31
- Cursor - exact: 31
- GitHub - other form: 35, 49

### `skills/sigma-setup/references/public-repo.md`

- Claude (not followed by Code) - other form: 155
- GitHub - exact: 4, 17, 81, 145, 174; other form: 120, 146

### `skills/sigma-setup/references/selection.md`

- GitHub - other form: 3

### `skills/sigma-slack/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 27
- Codex - exact: 7, 9
- Slack - exact: 3, 17, 18, 70, 74, 76; other form: 2, 3, 13, 20, 21, 33, 40, 46, 56, 68, 71, 75, 76

### `skills/sigma-slack/references/selection.md`

- Slack - exact: 3; other form: 1, 3

### `skills/sigma-status/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 17, 38
- Codex - exact: 7, 9
- GitHub - exact: 31; other form: 20, 21, 29

### `skills/sigma-time/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 25, 28, 34
- Codex - exact: 7, 9

### `skills/sigma-triage/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 230, 236, 240, 247, 248
- Codex - exact: 7, 9

### `skills/sigma-unpark/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 24, 34, 41, 56, 122
- Codex - exact: 7, 9
- GitHub - exact: 108, 140; other form: 35

### `skills/sigma-velocity/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 25, 27
- Codex - exact: 7, 9

### `skills/sigma-verify/SKILL.md`

- Claude Code - exact: 21
- Cursor - exact: 22

### `skills/sigma-vision/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 22, 38, 40
- Codex - exact: 7, 9

### `skills/sigma-vision/references/architecture.md`

- Claude (not followed by Code) - other form: 14, 27

### `skills/sigma-wizard/SKILL.md`

- Claude (not followed by Code) - exact: 11; other form: 9, 40, 51, 63, 69, 75
- Codex - exact: 7, 9
- GitHub - exact: 95, 120, 138, 140

## contract/


### `contract/README.md`

- GitHub - exact: 68, 83

### `contract/golden/config.json`

- GitHub - other form: 2

### `contract/golden/goal_frontmatter.md`

- GitHub - other form: 5

## evals/


### `evals/README.md`

- Claude Code - exact: 134
- Claude (not followed by Code) - exact: 37; other form: 21, 27, 31, 35, 40, 48, 61, 68, 69
- Codex - other form: 28
- Anthropic - other form: 30

### `evals/bench/launcher/README.md`

- Claude Code - exact: 23, 68, 96, 116
- Claude (not followed by Code) - exact: 78; other form: 7, 12, 13, 21, 22, 23, 25, 27, 28, 32, 33, 38, 40, 56, 64, 69, 71, 76, 77, 89, 98, 104, 105, 112, 116, 117, 118, 126, 129, 143, 148, 150, 153, 169, 192
- Codex - exact: 78; other form: 13, 27, 33, 40, 41
- Anthropic - other form: 117, 120, 125, 143, 166, 169

### `evals/bench/launcher/launcher.example.json`

- Claude (not followed by Code) - other form: 6

### `evals/bench/tasks/ext-bottle-1539/fetch.json`

- GitHub - other form: 2, 7, 8

### `evals/bench/tasks/ext-bottle-1539/task.json`

- GitHub - other form: 7

### `evals/bench/tasks/ext-lark-1618/fetch.json`

- GitHub - other form: 2, 7, 8

### `evals/bench/tasks/ext-lark-1630/fetch.json`

- GitHub - other form: 2, 7, 8

### `evals/bench/tasks/ext-more-itertools-1252/fetch.json`

- GitHub - other form: 2, 7, 8

### `evals/bench/tasks/ext-more-itertools-1304/fetch.json`

- GitHub - other form: 2, 7, 8

### `evals/bench/tasks/ext-more-itertools-1304/task.json`

- Cursor - other form: 7

### `evals/bench/tasks/ext-sqlparse-332/fetch.json`

- GitHub - other form: 2, 7, 8

### `evals/bench/tasks/ext-sqlparse-601/fetch.json`

- GitHub - other form: 2, 7, 8

### `evals/bench/tasks/ext-voluptuous-541/fetch.json`

- GitHub - other form: 2, 7, 8

### `evals/bench/tasks/manifest.json`

- Claude (not followed by Code) - exact: 8; other form: 5, 8, 24
- Cursor - other form: 112
- GitHub - other form: 28
- Anthropic - exact: 9

### `evals/bench/tasks/trap-1/task.json`

- Claude (not followed by Code) - other form: 7

### `evals/bench/tasks/trap-2/task.json`

- Claude (not followed by Code) - other form: 7

### `evals/bench/tasks/trap-3/task.json`

- Claude (not followed by Code) - other form: 7

## other


### `hooks/hooks.json`

- Claude (not followed by Code) - other form: 8, 17, 29, 41, 51, 56, 66, 77, 86, 97
