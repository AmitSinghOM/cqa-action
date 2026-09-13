# cqa-action

One-line GitHub Action for [cqa-analyzer](https://github.com/AmitSinghOM/code-quality-analyzer):
a deterministic, offline code-quality gate for Python, Go, TypeScript/JavaScript,
Java, Kotlin, C#, C/C++ and Rust. It fails the job on new findings, writes a
job summary, emits SARIF for code scanning, and never sends source code anywhere.

```yaml
- uses: AmitSinghOM/cqa-action@v1
```

That default gates the whole project on `warning`-or-higher findings with
`--strict`, and on pull requests restricts gating to the lines the PR changed
(the manifest is generated here from `git diff`; the analyzer itself never
invokes Git).

## Typical pull-request gate

```yaml
name: Code quality
on: [pull_request]
permissions:
  contents: read
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: AmitSinghOM/cqa-action@v1
        with:
          baseline: .code-quality-baseline.json      # optional: ignore pre-existing debt
          expect-config-fingerprint: ${{ vars.CQA_FINGERPRINT }}  # optional: a PR cannot weaken the gate
```

With code scanning (needs `security-events: write`):

```yaml
      - uses: AmitSinghOM/cqa-action@v1
        with:
          upload-sarif: "true"
```

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `path` | `.` | Directory to analyze |
| `version` | pinned per action release | Exact `cqa-analyzer` version to install |
| `deep` | `false` | Install the `[deep]` extra (tree-sitter duplication and complexity for Go, C/C++, Rust) |
| `fail-on` | `warning` | Fail on findings at this severity or higher; empty disables |
| `fail-under` | | Fail when the architecture signal score is below this |
| `strict` | `true` | Fail on any coverage gap |
| `baseline` | | Privacy-safe baseline written with `--write-baseline` |
| `new-findings-only` | `true` | With a baseline, only findings absent from it count |
| `changed-lines` | `auto` | `auto` (diff against PR base on `pull_request`), a manifest path, or `false` |
| `expect-config-fingerprint` | | sha256 the effective configuration must match |
| `config` | | Explicit config file |
| `sarif-file` | `code-quality-results.sarif` | SARIF output path; empty skips |
| `upload-sarif` | `false` | Upload SARIF to GitHub code scanning |
| `extra-args` | | Extra analyzer arguments |
| `python-version` | `3.12` | Interpreter used to run the analyzer |

## Outputs

`exit-code` (0 pass · 1 below threshold · 2 nothing analyzed · 3 coverage gap ·
4 findings · 5 score not applicable · 6 config mismatch), `verdict`, `score`,
`findings`, `sarif-file`, `configuration-fingerprint`.

GitHub drops a composite action's outputs and environment writes when the
action fails, which is exactly when you want them. The same values are
therefore also written to `$RUNNER_TEMP/cqa-results.json` (keys
`exit_code`, `verdict`, `score`, `findings`, `sarif_file`,
`configuration_fingerprint`), which later steps can read with `if: always()`.
On the success path the outputs above and `CQA_*` environment variables are
set as well. The job summary and the `::error::` annotation carry the
verdict on both paths.

## Guarantees

- The analyzer runs with `--offline`; the action makes exactly one network
  call of its own, `pip install` of the pinned version.
- Same ruleset, scoring policy and configuration fingerprint as the CLI and
  the `cqa-mcp` server, so an agent, a developer and CI see identical results.
- Every third-party action is pinned to a commit SHA.
- The changed-lines generator (`scripts/changed_lines.py`) is stdlib only and
  tested against the real analyzer.

## Versioning

`v1` is a moving major tag; `v1.x.y` tags are immutable. The default
`version` input advances with the action's minor releases and is recorded in
the release notes. Pin `version` yourself for byte-for-byte reproducibility.

## License

MIT.
