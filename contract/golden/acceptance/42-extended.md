---
kind: "acceptance"
goal: "42-extended"
verify_command: "python3 -m pytest tests/test_feature.py -q"
criteria_sha256: "1b31a2a2c4a46f58ac7453f476d6707dfd953648021de56f857fe865c5cbb392"
---
## Done when
- [ ] Valid input produces the documented result.
- [ ] Invalid input refuses before any write.
- [ ] Retrying an interrupted run preserves the result.

## Added criteria
- [ ] The retry path is covered by a test. (added: 42b; reason: review found the retry gap)
