# patch-label

<div align="center">

[English](README.md) | 中文

</div>

`patch-label` 面向 `thinkrepair-patch-diffs` 中的全部 Defects4J 样例，分别在原始 buggy 版本和应用 `thinkrepair.patch` 后的版本上完成：

1. checkout、编译目标项目；
2. 为 Defects4J 报告的修改类生成字节码级控制流图（CFG）；
3. 枚举测试方法，并逐测试执行完整测试集合；
4. 用 Java agent 记录测试经过的有序 CFG 基本块；
5. 把静态路径标为 `true`、`false` 或 `untested`；
6. 输出 `G = (nodes, edges)`、`SET(paths)`、`SET(labels)` 及原始测试轨迹。

默认会执行 `tests.all` 中每个可发现的测试方法。实验规模很大：当前数据集包含 205 个样例，每个样例有 `buggy` 和 `patched` 两个阶段。流水线按测试保存中间结果，可中断后继续。

## 工具选择

本项目优先使用少量、维护成熟且有明确官方文档的工具：

- [Defects4J](https://github.com/rjust/defects4j)：负责可复现 checkout、项目编译、单测执行和项目元数据导出。代码使用官方的 `classes.modified`、`tests.all`、`tests.relevant`、`cp.test` 和源码/二进制目录属性。
- [ASM](https://asm.ow2.io/) 9.8：构建 JVM 字节码基本块 CFG，并在完全相同的块边界插入运行时探针。ASM 只在构建辅助 JAR 时下载，不是 Python 运行时依赖。
- [JUnit 4](https://junit.org/junit4/) 4.13.2：通过 `Request.aClass(...).getRunner().getDescription()` 获取 JUnit 3/4 runner 的叶子测试描述；实际执行仍交给 Defects4J，保留项目自己的构建和 runner 行为。
- [uv](https://docs.astral.sh/uv/)：创建虚拟环境、锁定依赖并执行全部项目命令。
- Python 运行时代码只使用标准库；开发依赖只有 `pytest`。

没有使用 JaCoCo 作为路径数据源。JaCoCo 很适合统计行/分支覆盖率，但覆盖集合不保留基本块执行顺序，无法直接满足逐测试 CFG 路径标注。

## 环境准备

Defects4J 当前版本要求 Java 11、Git、Subversion 和 Perl。官方推荐 `cpanm`；本项目找不到 `cpanm` 时会回退到系统 `cpan -T`，读取 `cpanfile` 安装相同模块。运行时会固定 `TZ=America/Los_Angeles`，与 Defects4J 官方可复现性要求一致。若系统默认 Java 不是 11，本项目会优先采用 `javac` 对应的 Java 11；也可显式设置 `PATCH_LABEL_JAVA_HOME`。

克隆并初始化：

```bash
git clone https://github.com/rjust/defects4j.git tools/defects4j
uv sync --dev
uv run patch-label doctor
uv run patch-label init-defects4j
uv run patch-label build-helper
```

仓库中已经存在 `tools/defects4j` 时不要重复 clone。`init-defects4j` 会先执行 `cpanm --installdeps .`，然后执行 Defects4J 的 `init.sh`。若 Perl 依赖已经安装，可使用：

```bash
uv run patch-label init-defects4j --skip-perl-deps
```

所有 Python/实验入口均通过 `uv run` 调用；不需要手动激活 `.venv`。

## 数据集检查

列出全部样例：

```bash
uv run patch-label catalog
```

输出机器可读目录：

```bash
uv run patch-label catalog --output results/catalog.json
```

选择器可以写成 `Compress-44`，也可以写成不会产生版本歧义的 `D4JV2.0/Compress-44`。

## 运行实验

先用少量相关测试做冒烟验证：

```bash
uv run patch-label run \
  --example D4JV2.0/Compress-44 \
  --phase both \
  --test-scope relevant \
  --max-tests 5
```

对一个样例执行完整实验：

```bash
uv run patch-label run \
  --example D4JV2.0/Compress-44 \
  --phase both \
  --test-scope all
```

省略 `--example` 会顺序处理全部 205 个样例：

```bash
uv run patch-label run --phase both --test-scope all --keep-going
```

常用选项：

- `--phase buggy|patched|both`：选择实验阶段，默认 `both`。
- `--test-scope all|relevant|trigger`：默认 `all`，严格对应“所有测试用例”；`relevant` 和 `trigger` 只适合开发验证，其中 `trigger` 直接选择暴露原始 bug 的测试。
- `--max-tests N`：限制发现结果中的前 N 个测试，仅用于冒烟测试。
- `--test-timeout SECONDS`：单测试超时，默认 600 秒。
- `--max-loop-visits N`：静态路径枚举时每个普通节点最多出现次数，默认 2。
- `--max-paths-per-method N`：每个方法最多保存的静态路径数，默认 1000；触发限制会写入 `path_enumeration_truncations`。
- `--fresh`：删除并重建由本工具管理的 checkout 和测试缓存。
- `--no-resume`：忽略已有结果重新执行；默认按测试恢复。
- `--keep-going`：一个样例失败后继续，最终把错误写入 `results/failures.json`。

## CFG 和路径定义

### 图 `G`

图覆盖 `classes.modified` 中的类及其嵌套类。每个非抽象、非 native 方法生成一个方法内 CFG：

- 节点是 JVM 字节码基本块，并附带类名、方法名、descriptor、字节码指令区间和源码行区间；
- 每个方法有虚拟 `ENTRY`、`EXIT` 节点；
- 边包括 `entry`、`fallthrough`、`jump`、`switch-case`、`switch-default`、`exit`、`throw` 和异常处理边；
- try 区域的异常边是保守近似：区域内基本块都连接到对应 handler；
- 图是方法内 CFG，不创建调用图边。

字节码 CFG 比仅从源码语法推断更贴近实际 JVM 控制转移，也能统一处理不同 Defects4J 项目的 Java 源码版本。静态分析器与 Java agent 共用 `CfgBuilder`，因此节点 ID 不需要通过源码行号猜测匹配。

### `SET(paths)`

带循环 CFG 的全部路径是无限集合，因此实验必须给出有限、可复现的路径覆盖准则。本项目枚举从 `ENTRY` 到 `EXIT` 的有界路径：

- 普通节点最多访问 `--max-loop-visits` 次；
- 每个方法最多保留 `--max-paths-per-method` 条；
- 达到上限的方法会显式记录，不会把截断结果冒充完整路径集。

此外，`observed_paths` 保留测试实际产生的基本块序列，不受静态路径枚举截断影响。

### `SET(labels)`

每个静态路径与测试结果按以下规则关联：

- 路径出现在某个测试的方法调用轨迹中，且该测试成功：`{"status": "true", "test_id": ...}`；
- 路径出现在某个失败或超时测试中：`{"status": "false", "test_id": ...}`；
- 没有任何测试覆盖该路径：`{"status": "untested", "test_id": null}`。

同一路径可以有多个标签，因为多个测试可能覆盖它。`true` 的判定要求 Defects4J 命令返回 0 且 `failing_tests` 为空。测试方法发现失败时会降级为测试类级执行，并在 `test_discovery_errors` 中记录；这类结果的 `granularity` 为 `class`，不会静默伪装成方法级结果。

`coverage` 同时给出 `covered_nodes`、`untested_nodes`、`covered_edges` 和 `untested_edges`，便于不依赖路径枚举上限地检查结构覆盖。

## 补丁应用

数据集中的补丁使用 `original/Project-ID.java` 和 `repaired/Project-ID.java` 这类占位文件名，不能直接 `git apply`。`patched` 阶段会：

1. 用 `classes.modified` 定位候选源码文件；
2. 用每个 unified-diff hunk 的旧文本做唯一上下文匹配；
3. 精确匹配失败后只尝试一次空白归一化匹配；
4. 匹配为 0 个或多个位置时终止该样例，避免修改错误位置；
5. 把文件、行号和匹配模式写入 `patch-application.json`。

## 输出结构

每个阶段的最终文件位于：

```text
results/<dataset-version>/<Project-ID>/<buggy|patched>/experiment.json
```

同目录还包含：

```text
graph.json                  原始 CFG
graph.log                   图生成日志
compile.log                 编译日志
test-manifest.json          测试发现结果
patch-application.json      patched 阶段的补丁定位记录
tests/*.json.gz             可恢复的逐测试结果和轨迹
test-logs/*.log             Defects4J 逐测试完整输出
experiment.json             聚合后的 G、路径集、标签集和摘要
```

`experiment.json` 的核心形状：

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

### 已包含的示例结果

仓库包含一次经过验证的 `D4JV2.0/Compress-44` trigger 测试运行，可以直接在 GitHub 上查看具体的 CFG、动态轨迹、路径集和路径标签：

- [buggy `experiment.json`](results/D4JV2.0/Compress-44/buggy/experiment.json)：空参数构造函数测试经过 `ENTRY -> B0 -> EXIT`，标签为 `false`；
- [patched `experiment.json`](results/D4JV2.0/Compress-44/patched/experiment.json)：补丁增加空值检查分支并抛出预期异常，同一测试的标签变为 `true`。

该示例使用 `--test-scope trigger --max-tests 1`，目的是提供紧凑、可复现的结果；它不是完整的 `--test-scope all` 实验。

## 开发与验证

```bash
uv run pytest
uv run patch-label catalog --output .patch-label/catalog.json
uv run patch-label build-helper --force
uv run patch-label doctor
```

辅助 JAR、项目 checkout 和缓存位于 `.patch-label/`；最终实验数据位于 `results/`。两个目录中新生成的内容默认忽略；适合公开的结果快照可以像已包含的 `Compress-44` 示例一样显式加入仓库。
