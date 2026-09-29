# patch-label

<div align="center">

English | [中文](README_zh.md)

</div>

## Project Introduction

`patch-label` runs the `thinkrepair-patch-diffs` Defects4J examples in two phases: the original buggy checkout and the checkout after applying `thinkrepair.patch`. It builds bytecode-level control-flow graphs (CFGs) for every class listed by Defects4J as modified, runs selected tests one at a time, records the ordered basic blocks reached by each test, and assigns labels to complete `ENTRY -> ... -> EXIT` paths.

Labels always describe a complete path, never an individual CFG node:

| Label | Meaning |
| --- | --- |
| `true` | The covering test passed. This includes an expected exception represented by an incomplete invocation. |
| `false` | The covering test failed. This includes an unexpected exception represented by an incomplete invocation. |
| `unknown` | The test timed out, ended with an execution error, or has no definitive pass/fail outcome. |
| `untested` | No selected test observed the path. It is retained in machine-readable data but omitted from the concise Markdown report. |

No LLM service or LLM API is used anywhere in the experiment pipeline.

### Static patch labels

After the regular experiments have produced both `buggy` and `patched` results, run:

```bash
uv run patch-label analyze-patches \
  --output-dir results \
  --dataset-dir thinkrepair-patch-diffs \
  --analysis-output results/patch-label-analysis.json
```

This command analyzes every example for which both phases and the patch file are present. It does not rerun Defects4J tests. It uses the recorded CFGs, path-level labels, patch hunks, and patch-application line locations. Z3 is used to validate the conjunction of CFG edges for each reported witness path.

The three independent tests are conservative and operate on complete paths. A
test stops immediately after its first decisive witness; later candidates are
not evaluated for that label.

| Test | `confirmed` evidence |
| --- | --- |
| `root_cause_not_fixed` | The same false path remains after patching, or a new patch branch still has a satisfiable false-path witness with the same path context outside the patch. |
| `boundary_incomplete` | An original `false` path reaches a changed patch unit, and the solver finds a model for the symmetric difference `(C_old AND NOT C_new) OR (NOT C_old AND C_new)`. `C_old` and `C_new` contain only abstract CFG branch identities and edge polarities; they intentionally do not encode concrete Java values. |
| `overrepair` | The patch has at least two units. First, a unit absent from every original `false` path immediately confirms the label. Otherwise, each unit is reverted alone from a fresh all-units-applied state: an unreachable rollback is skipped; a reachable rollback confirms the label only when its symbolic output is equivalent to the full patch output. |

Each test stops as soon as it obtains decisive evidence. `not_confirmed` means the criterion was not established. `inconclusive` is used when proving the criterion requires symbolic output equivalence that the recorded CFG does not contain; it is never silently converted into a positive label. Results and evidence are written to `patch-label-analysis.json`, with a concise table in `patch-label-analysis.md`.

Existing results use the schema 2.0 CFG, which stores nodes, edges, source
locations, and edge kinds. The analyzer assigns each branching CFG node an
abstract identity and each selected outgoing edge a polarity (`true`, `false`,
`case:<index>`, or `default`). It conjoins those abstract choices over the
complete path. No concrete Java input value is inferred, so this analysis does
not claim to solve for `x = 5` or `x = null`. It does not rerun or modify the
existing experiment data. Missing symbolic output information remains
`inconclusive`, never a positive label. Each confirmed result contains
`stopped_after` and an `evidence` object with path IDs, patch-unit IDs, the
solver formula, satisfiability status, model, and (for overrepair) every
rollback attempt made before confirmation.

### Result directory

Each phase writes to a fingerprinted directory so that different run configurations do not overwrite one another:

```text
results/<dataset-version>/<Project-ID>/<buggy|patched>/runs/<run-fingerprint>/
```

The primary outputs are deliberately separated into CFG structure and path labels, following the same general separation of program components and test observations used by spectrum-based fault-localization datasets:

| File | Contents |
| --- | --- |
| `cfg.dot` | One complete Graphviz directed graph containing every CFG node and edge. Methods are grouped into subgraphs. This is plain text and does not require Graphviz to be generated. |
| `graph.json` | The same complete CFG in machine-readable form. No node or edge is omitted. |
| `paths.csv` | Every complete path and every associated path-test label record, including paths not reached by the selected tests. |
| `report.md` | A concise human-readable summary, links to the complete CFG, and all paths labeled `true`, `false`, or `unknown`. Paths with no test evidence are intentionally hidden here. |
| `experiment.json` | The full aggregate used to reproduce reports. It also contains configuration, summaries, coverage sets, diagnostics, and test records. |

Supporting files include `compile.log`, `graph.log`, `test-manifest.json`, `run-manifest.json`, `tests/*.json.gz`, `test-logs/*.log`, and, for the patched phase, `patch-application.json`. They exist for diagnostics and safe resume; the main experimental data is in the five primary outputs above.

### Complete CFG values

`graph.json` has two arrays:

```json
{
  "nodes": [],
  "edges": []
}
```

Every node contains:

| Field | Value |
| --- | --- |
| `id` | Globally unique node identifier. It combines the method identifier with `ENTRY`, `EXIT`, or a basic-block name such as `B3`. |
| `method_id` | Fully qualified class name, method name, and JVM descriptor. |
| `class_name` | Fully qualified Java class name. |
| `method_name` | Java/JVM method name; constructors use `<init>`. |
| `descriptor` | JVM method descriptor containing parameter and return types. |
| `block_index` | Zero-based basic-block number; virtual nodes use `-1`. |
| `start_instruction`, `end_instruction` | Inclusive bytecode instruction-index interval; virtual nodes use `-1`. |
| `start_line`, `end_line` | Inclusive source-line interval when debug line metadata is available; unavailable values use `-1`. |
| `instruction_count` | Number of bytecode instructions in the basic block; virtual nodes use `0`. |
| `virtual` | `true` for synthetic `ENTRY`/`EXIT` nodes and `false` for executable basic blocks. |

Every edge contains `source`, `target`, and `kind`. `source` and `target` are node IDs. `kind` is one of `entry`, `fallthrough`, `jump`, `switch-case`, `switch-default`, `exit`, `throw`, or an exception-handler edge kind emitted by the bytecode CFG builder. CFGs are intraprocedural: calls do not create edges into another method.

`cfg.dot` contains exactly these node and edge sets. A real basic-block label includes its block name, source-line interval, and bytecode instruction interval. Synthetic `ENTRY` and `EXIT` nodes are shown as ovals.

### Path and label values

`paths.csv` uses one row per path-test label record:

| Column | Value |
| --- | --- |
| `path_id` | Stable hash-based identifier for the method and complete node sequence. |
| `method_id` | Method whose intraprocedural CFG contains the path. |
| `whole_path` | Complete ordered node sequence, for example `ENTRY -> B0[L33-34] -> B1[L35] -> EXIT`. |
| `label` | `true`, `false`, `unknown`, or `untested`, using the definitions above. |
| `observation` | `observed` for a complete runtime path, `incomplete` when an invocation ended before its normal exit, or `not_observed` when no selected test reached the path. |
| `test_outcome` | `passed`, `failed`, `timed_out`, or `error`; empty for a path with no covering test. |
| `test_id` | Defects4J test selector in `Class::method` form; empty for a path with no covering test. |

The same path can appear in multiple rows because multiple tests may cover it. A passing and a failing test may therefore produce separate `true` and `false` records for the same path. Loops make the theoretical path set infinite, so static paths are bounded by `--max-loop-visits` and `--max-paths-per-method`. Any complete runtime path missing from the bounded static set is still added to the output.

## Dependencies

For the two input directories, use their matching Defects4J releases: `D4JV1.2` requires `tools/defects4j-v1.2` at tag `v1.2.0` with Java 7, and `D4JV2.0` requires `tools/defects4j-v2.0` at tag `v2.0.0` with Java 8. The Java helper itself is built with a newer JDK. See [Ubuntu versioned setup](docs/ubuntu-versioned-setup.md) for the verified setup and checks. `run` selects the correct release for each example automatically.

The project intentionally keeps its dependency set small:

| Dependency | Purpose |
| --- | --- |
| Python `>=3.11` | Runs the experiment coordinator, report generator, and static patch analyzer. |
| `z3-solver` | Checks satisfiability of abstract CFG branch-choice formulas and structural witness constraints. |
| `uv` | Creates the virtual environment, installs the project, locks dependencies, and runs every Python command. |
| Defects4J v1.2.0 and v2.0.0 | Check out the matching dataset version, export metadata, compile projects, and run tests. Keep both clones inside `tools/`. |
| Java 7, 8, and 11+ JDKs | Java 7/8 run the corresponding Defects4J projects; JDK 11+ compiles the Java helper. |
| Git, Subversion, Perl | Required by Defects4J. `cpanm` is preferred; `cpan` is supported as a fallback. |
| ASM 9.8 | Builds bytecode CFGs and inserts probes at the same basic-block boundaries. Downloaded only while building the helper JAR. |
| JUnit 4.13.2 | Discovers JUnit 3/4 leaf tests for the helper. Test execution remains under Defects4J. |
| `pytest` | Development-only test dependency installed by `uv sync --dev`. |

The analyzer adds only `z3-solver`; the experiment runner otherwise uses the Python standard library. Generating `cfg.dot` adds no dependency.

## Running

Set up the two historical Defects4J releases using [Ubuntu versioned setup](docs/ubuntu-versioned-setup.md), then run:

```bash
uv sync --dev
uv run patch-label doctor
uv run patch-label build-helper
```

Run a five-test smoke experiment:

```bash
uv run patch-label run \
  --example D4JV2.0/Compress-44 \
  --phase both \
  --test-scope relevant \
  --max-tests 5
```

Run all tests for one example in both phases:

```bash
uv run patch-label run \
  --example D4JV2.0/Compress-44 \
  --phase both \
  --test-scope all
```

Run the complete experiment for every catalogued example:

```bash
uv run patch-label run --phase both --test-scope all --jobs 4 --keep-going
```

`--jobs` parallelizes independent examples, not tests inside one example. Each worker uses a separate checkout and output directory, while the buggy and patched phases of the same example remain sequential. Start with `--jobs 4`; higher values may help on machines with enough CPU, memory, and disk bandwidth, but they do not reduce final disk usage.

Regenerate `report.md`, `cfg.dot`, and `paths.csv` from an existing aggregate without rerunning Defects4J tests:

```bash
uv run patch-label report results/.../experiment.json
```

## Command Reference

All commands have this form:

```text
uv run patch-label <command> [common path options] [command options]
```

Common path options are accepted by every command:

| Option | Default | Meaning |
| --- | --- | --- |
| `--repo-root PATH` | Current directory | Repository root used to resolve all relative paths. |
| `--dataset-dir PATH` | `thinkrepair-patch-diffs` | ThinkRepair patch-diff dataset directory. |
| `--defects4j-v1-dir PATH` | `tools/defects4j-v1.2` | Defects4J v1.2.0 clone used for D4JV1.2. |
| `--defects4j-v2-dir PATH` | `tools/defects4j-v2.0` | Defects4J v2.0.0 clone used for D4JV2.0. |
| `--defects4j-dir PATH` | `tools/defects4j` | Legacy initialization target; `run` uses the two versioned options above. |
| `--state-dir PATH` | `.patch-label` | Generated helper JARs, checkouts, and reusable state. |
| `--output-dir PATH` | `results` | Final result root. |

### `doctor`

```text
uv run patch-label doctor [--json] [common path options]
```

Checks executables, the dataset, both historical Defects4J runtimes, Java JDKs, and all catalogued source revisions. `--json` prints the checks as JSON instead of a readable list. The command exits nonzero if a required check fails.

### `catalog`

```text
uv run patch-label catalog [--output FILE] [common path options]
```

Lists all discovered examples. `--output FILE` writes the complete catalog as JSON instead of printing only selectors. An example selector is either `Project-ID`, such as `Compress-44`, or the unambiguous `VERSION/Project-ID`, such as `D4JV2.0/Compress-44`.

### `init-defects4j`

```text
uv run patch-label init-defects4j [--skip-perl-deps] [common path options]
```

Initializes the existing Defects4J clone. By default it installs modules from Defects4J's `cpanfile` and then runs `init.sh`. `--skip-perl-deps` runs only `init.sh` when the Perl modules are already available.

### `build-helper`

```text
uv run patch-label build-helper [--force] [common path options]
```

Builds the ASM/JUnit helper and Java agent. A matching cached JAR is reused by default. `--force` rebuilds it even when its fingerprint is current.

### `run`

```text
uv run patch-label run \
  [--example SELECTOR ...] \
  [--phase buggy|patched|both] \
  [--test-scope all|relevant|trigger] \
  [--max-tests N] \
  [--compile-timeout SECONDS] \
  [--test-timeout SECONDS] \
  [--discovery-timeout SECONDS] \
  [--max-loop-visits N] \
  [--max-paths-per-method N] \
  [--jobs N] \
  [--fresh] [--no-resume] [--keep-going] \
  [common path options]
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--example SELECTOR` | All examples | Selects one example. Repeat the option to select several. |
| `--phase buggy|patched|both` | `both` | Runs the original version, the ThinkRepair-patched version, or both. |
| `--test-scope all|relevant|trigger` | `all` | Uses all tests, Defects4J relevant tests, or official triggering tests. Complete experiments should use `all`; the others are for targeted checks. |
| `--max-tests N` | No limit | Runs only the first `N` discovered tests. Intended for smoke tests, not final data. |
| `--compile-timeout SECONDS` | `1800` | Maximum time for project compilation. |
| `--test-timeout SECONDS` | `600` | Maximum time for each individual test. |
| `--discovery-timeout SECONDS` | `600` | Maximum time for test-method discovery. |
| `--max-loop-visits N` | `2` | Maximum appearances of an ordinary CFG node in one statically enumerated path. |
| `--max-paths-per-method N` | `1000` | Maximum stored static paths per method. Limit hits are recorded in the aggregate. |
| `--jobs N` | `1` | Runs up to `N` independent examples concurrently. Tests and phases within one example remain sequential. |
| `--fresh` | Off | Deletes and rebuilds tool-managed output/checkouts for the selected fingerprint. It does not modify the Defects4J repository itself. |
| `--no-resume` | Off | Disables reuse of matching per-test results and reruns the selected phase. |
| `--keep-going` | Off | Continues with later examples after a failure and writes `results/failures.json`. |

### `report`

```text
uv run patch-label report EXPERIMENT_JSON [common path options]
```

Reads a schema 2.0 `experiment.json` and regenerates the three readable/export files beside it: `report.md`, `cfg.dot`, and `paths.csv`. It does not compile a project, execute tests, apply a patch, or call any external API.

### `analyze-patches`

```text
uv run patch-label analyze-patches \
  [--analysis-output FILE] [common path options]
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--analysis-output FILE` | `results/patch-label-analysis.json` | JSON output containing one result and evidence object per patch package. A Markdown summary is written beside it. |

The command stops each of the three independent tests after decisive evidence is found. It never changes an existing experiment result.
