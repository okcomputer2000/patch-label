# patch-label

<div align="center">

English | [中文](README_zh.md)

</div>

`patch-label` processes every Defects4J example in `thinkrepair-patch-diffs`, both before and after applying its `thinkrepair.patch`. For each phase, it:

1. checks out and compiles the target project;
2. builds bytecode-level control-flow graphs (CFGs) for the classes reported as modified by Defects4J;
3. discovers test methods and runs the selected test suite one test at a time;
4. uses a Java agent to record the ordered CFG basic blocks visited by each test;
5. labels static paths as `true`, `false`, or `untested`; and
6. writes `G = (nodes, edges)`, `SET(paths)`, `SET(labels)`, and the raw execution traces.

By default, the pipeline runs every discoverable test method from `tests.all`. The experiment is intentionally large: the current dataset contains 205 examples, and every example has both a `buggy` and a `patched` phase. Per-test intermediate results are persisted so interrupted runs can resume.

## Tooling

The project deliberately uses a small set of mature tools with clear upstream documentation:

- [Defects4J](https://github.com/rjust/defects4j) provides reproducible checkouts, project compilation, test execution, and project metadata. The implementation uses the official `classes.modified`, `tests.all`, `tests.relevant`, `cp.test`, and source/binary directory properties.
- [ASM](https://asm.ow2.io/) 9.8 builds JVM bytecode basic-block CFGs and inserts runtime probes at exactly the same block boundaries. ASM is downloaded only when the helper JAR is built; it is not a Python runtime dependency.
- [JUnit 4](https://junit.org/junit4/) 4.13.2 discovers JUnit 3/4 leaf test descriptions through `Request.aClass(...).getRunner().getDescription()`. Defects4J still performs the actual execution, preserving each project's own build and test runner behavior.
- [uv](https://docs.astral.sh/uv/) creates the virtual environment, locks dependencies, and runs every project command.
- The Python runtime uses only the standard library. The sole development dependency is `pytest`.

JaCoCo is not used as the path data source. JaCoCo is well suited to line and branch coverage, but a coverage set does not preserve the execution order of basic blocks and therefore cannot directly support per-test CFG path labeling.

## Environment Setup

The current Defects4J release requires Java 11, Git, Subversion, and Perl. Defects4J recommends `cpanm`; if `cpanm` is unavailable, this project falls back to the system `cpan -T` command and installs the same modules from `cpanfile`. The pipeline fixes `TZ=America/Los_Angeles` to satisfy Defects4J's reproducibility requirements. If the default Java installation is not Java 11, the project prefers the Java 11 installation associated with `javac`; `PATCH_LABEL_JAVA_HOME` can also be set explicitly.

Clone and initialize the dependencies:

```bash
git clone https://github.com/rjust/defects4j.git tools/defects4j
uv sync --dev
uv run patch-label doctor
uv run patch-label init-defects4j
uv run patch-label build-helper
```

Do not clone Defects4J again if `tools/defects4j` already exists. `init-defects4j` first runs `cpanm --installdeps .` and then Defects4J's `init.sh`. If the Perl dependencies are already installed, use:

```bash
uv run patch-label init-defects4j --skip-perl-deps
```

All Python and experiment entry points are invoked through `uv run`; manually activating `.venv` is unnecessary.

## Dataset Inspection

List every example:

```bash
uv run patch-label catalog
```

Write a machine-readable catalog:

```bash
uv run patch-label catalog --output results/catalog.json
```

An example selector can be written as `Compress-44`, or as the version-qualified and unambiguous `D4JV2.0/Compress-44`.

## Running Experiments

Start with a small set of relevant tests for a smoke test:

```bash
uv run patch-label run \
  --example D4JV2.0/Compress-44 \
  --phase both \
  --test-scope relevant \
  --max-tests 5
```

Run the complete experiment for one example:

```bash
uv run patch-label run \
  --example D4JV2.0/Compress-44 \
  --phase both \
  --test-scope all
```

Omitting `--example` processes all 205 examples sequentially:

```bash
uv run patch-label run --phase both --test-scope all --keep-going
```

Common options:

- `--phase buggy|patched|both`: selects the experiment phase; the default is `both`.
- `--test-scope all|relevant|trigger`: defaults to `all`, which strictly means every test case. `relevant` and `trigger` are intended only for development checks; `trigger` directly selects the tests that expose the original bug.
- `--max-tests N`: limits execution to the first `N` discovered tests and is intended only for smoke tests.
- `--test-timeout SECONDS`: sets the per-test timeout; the default is 600 seconds.
- `--max-loop-visits N`: limits how many times an ordinary node may appear in an enumerated static path; the default is 2.
- `--max-paths-per-method N`: limits each method to at most `N` stored static paths; the default is 1000. Any truncation is recorded in `path_enumeration_truncations`.
- `--fresh`: deletes and rebuilds the checkouts and test caches managed by this tool.
- `--no-resume`: ignores existing intermediate results and reruns tests; resuming is enabled by default.
- `--keep-going`: continues after an example fails and writes the collected errors to `results/failures.json`.

## CFG and Path Definitions

### Graph `G`

The graph covers every class in `classes.modified`, including nested classes. Each non-abstract, non-native method receives its own intraprocedural CFG:

- nodes are JVM bytecode basic blocks with class name, method name, descriptor, bytecode instruction range, and source line range metadata;
- every method has virtual `ENTRY` and `EXIT` nodes;
- edges include `entry`, `fallthrough`, `jump`, `switch-case`, `switch-default`, `exit`, `throw`, and exception-handler edges;
- exception edges for `try` regions are a conservative approximation: every basic block in the region is connected to its handler; and
- the graph is intraprocedural and does not contain call-graph edges.

A bytecode CFG reflects actual JVM control transfer more closely than a CFG inferred only from source syntax and works consistently across the Java source versions used by different Defects4J projects. The static analyzer and Java agent share the same `CfgBuilder`, so node IDs do not need to be matched heuristically through source line numbers.

### `SET(paths)`

Because a CFG with loops has infinitely many possible paths, the experiment uses a finite and reproducible path-enumeration rule. It enumerates bounded paths from `ENTRY` to `EXIT`:

- an ordinary node may be visited at most `--max-loop-visits` times;
- each method stores at most `--max-paths-per-method` paths; and
- methods that reach the limit are recorded explicitly, rather than presenting a truncated set as complete.

In addition, `observed_paths` preserves the basic-block sequences actually produced by tests and is not constrained by the static path-enumeration limit.

### `SET(labels)`

Each static path is associated with test outcomes according to these rules:

- if a path appears in a test's method trace and the test passes: `{"status": "true", "test_id": ...}`;
- if a path appears in a failing or timed-out test: `{"status": "false", "test_id": ...}`; and
- if no test covers the path: `{"status": "untested", "test_id": null}`.

The same path can have multiple labels because multiple tests may cover it. A `true` outcome requires both a zero Defects4J command exit code and an empty `failing_tests` list. If method-level test discovery fails, execution falls back to the test class and records the error in `test_discovery_errors`; these results use `"granularity": "class"` and are never silently presented as method-level results.

The `coverage` object also lists `covered_nodes`, `untested_nodes`, `covered_edges`, and `untested_edges`, allowing structural coverage to be inspected independently of the static path-enumeration bound.

## Patch Application

Dataset patches use placeholder file names such as `original/Project-ID.java` and `repaired/Project-ID.java`, so they cannot be passed directly to `git apply`. During the `patched` phase, the pipeline:

1. uses `classes.modified` to locate candidate source files;
2. uniquely matches each unified-diff hunk by its original text;
3. attempts whitespace-normalized matching once if exact matching fails;
4. stops the example when a hunk matches zero or multiple locations, avoiding modifications to the wrong source location; and
5. records the file, line number, and matching mode in `patch-application.json`.

## Output Layout

The final result for each phase is written to:

```text
results/<dataset-version>/<Project-ID>/<buggy|patched>/experiment.json
```

The same directory also contains:

```text
graph.json                  Raw CFG
graph.log                   Graph-generation log
compile.log                 Compilation log
test-manifest.json          Test-discovery result
patch-application.json      Patch-location record for the patched phase
tests/*.json.gz             Resumable per-test results and traces
test-logs/*.log             Complete per-test Defects4J output
experiment.json             Aggregated graph, path set, label set, and summary
```

The core shape of `experiment.json` is:

```json
{
  "graph": {
    "nodes": [],
    "edges": []
  },
  "path_set": [
    {"id": "path:...", "method_id": "...", "nodes": [], "kind": "bounded-entry-exit"}
  ],
  "labels": [
    {"path_id": "path:...", "status": "true", "test_id": "Class::method"},
    {"path_id": "path:...", "status": "untested", "test_id": null}
  ],
  "observed_paths": [],
  "coverage": {},
  "tests": [],
  "summary": {}
}
```

### Included Example Result

The repository includes a verified trigger-test run for `D4JV2.0/Compress-44`, making it possible to inspect a concrete CFG, dynamic trace, path set, and path labels directly on GitHub:

- [buggy `experiment.json`](results/D4JV2.0/Compress-44/buggy/experiment.json): the null-argument constructor test follows `ENTRY -> B0 -> EXIT` and is labeled `false`;
- [patched `experiment.json`](results/D4JV2.0/Compress-44/patched/experiment.json): the applied null checks add branches, the expected exception is thrown, and the same test is labeled `true`.

This included result uses `--test-scope trigger --max-tests 1` as a compact, reproducible example. It is not a full `--test-scope all` experiment.

## Development and Verification

```bash
uv run pytest
uv run patch-label catalog --output .patch-label/catalog.json
uv run patch-label build-helper --force
uv run patch-label doctor
```

The helper JAR, project checkouts, and caches are stored under `.patch-label/`. Final experiment data is stored under `results/`. Newly generated contents of both directories are ignored by default; publishable result snapshots may be added explicitly, as with the included `Compress-44` example.
