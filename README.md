# patch-label

<div align="center">

English | [中文](README_zh.md)

</div>

## Project Introduction

`patch-label` runs the `thinkrepair-patch-diffs` Defects4J examples in two phases: the original buggy checkout and the checkout after applying `thinkrepair.patch`. It builds bytecode-level control-flow graphs (CFGs) for every class listed by Defects4J as modified, runs selected tests one at a time, records the ordered basic blocks reached by each test, and assigns labels to complete `ENTRY -> ... -> EXIT` paths.

Labels always describe a complete path, never an individual CFG node:

| Label | Meaning |
| --- | --- |
| `true` | A passing test observed this complete path. |
| `false` | A failing test observed this complete path. |
| `unknown` | The path may be relevant, but its evidence is incomplete, or the covering test timed out or ended with an execution error. |
| `untested` | No selected test observed the path. It is retained in machine-readable data but omitted from the concise Markdown report. |

No LLM service or LLM API is used anywhere in the experiment pipeline.

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
| `observation` | `observed` for a complete runtime path, `unknown` for path-specific incomplete evidence, or `not_observed` when no selected test reached the path. |
| `test_outcome` | `passed`, `failed`, `timed_out`, or `error`; empty for a path with no covering test. |
| `test_id` | Defects4J test selector in `Class::method` form; empty for a path with no covering test. |

The same path can appear in multiple rows because multiple tests may cover it. A passing and a failing test may therefore produce separate `true` and `false` records for the same path. Loops make the theoretical path set infinite, so static paths are bounded by `--max-loop-visits` and `--max-paths-per-method`. Any complete runtime path missing from the bounded static set is still added to the output.

## Dependencies

The project intentionally keeps its dependency set small and unchanged:

| Dependency | Purpose |
| --- | --- |
| Python `>=3.11` | Runs the experiment coordinator and report generator. Runtime code uses only the Python standard library. |
| `uv` | Creates the virtual environment, installs the project, locks dependencies, and runs every Python command. |
| Defects4J | Checks out projects, exports metadata, compiles projects, and runs tests. Clone it inside this repository; do not modify Defects4J. |
| Java 11 JDK | Compiles Defects4J projects and the Java helper. Set `PATCH_LABEL_JAVA_HOME` if Java 11 is not selected automatically. |
| Git, Subversion, Perl | Required by Defects4J. `cpanm` is preferred; `cpan` is supported as a fallback. |
| ASM 9.8 | Builds bytecode CFGs and inserts probes at the same basic-block boundaries. Downloaded only while building the helper JAR. |
| JUnit 4.13.2 | Discovers JUnit 3/4 leaf tests for the helper. Test execution remains under Defects4J. |
| `pytest` | Development-only test dependency installed by `uv sync --dev`. |

There are no Python runtime packages beyond the standard library. Generating `cfg.dot` adds no dependency.

## Running

From a parent directory, clone both repositories so Defects4J is placed at `patch-label/tools/defects4j`:

```bash
git clone https://github.com/okcomputer2000/patch-label.git
cd patch-label
git clone https://github.com/rjust/defects4j.git tools/defects4j
uv sync --dev
uv run patch-label doctor
uv run patch-label init-defects4j
uv run patch-label build-helper
```

If Defects4J has already been cloned and initialized, do not clone or modify it again. If its Perl modules are already installed, initialization can skip that step:

```bash
uv run patch-label init-defects4j --skip-perl-deps
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
| `--defects4j-dir PATH` | `tools/defects4j` | Unmodified Defects4J clone. |
| `--state-dir PATH` | `.patch-label` | Generated helper JARs, checkouts, and reusable state. |
| `--output-dir PATH` | `results` | Final result root. |

### `doctor`

```text
uv run patch-label doctor [--json] [common path options]
```

Checks executables, the dataset, the Defects4J clone and initialization, and Java 11 availability. `--json` prints the checks as JSON instead of a readable list. The command exits nonzero if a required check fails.

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
