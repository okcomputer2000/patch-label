# patch-label

<div align="center">

[English](README.md) | 中文

</div>

## 项目介绍

`patch-label` 对 `thinkrepair-patch-diffs` 中的 Defects4J 样例执行两个阶段的实验：原始 buggy 版本，以及应用 `thinkrepair.patch` 后的 patched 版本。程序为 Defects4J 标记为修改过的全部类生成字节码级控制流图（CFG），逐个运行选定测试，记录每个测试实际经过的有序基本块，并为完整的 `ENTRY -> ... -> EXIT` 路径分配标签。

标签始终属于整条完整路径，绝不属于单个 CFG 节点：

| 标签 | 含义 |
| --- | --- |
| `true` | 一个通过的测试观测到了这条完整路径。 |
| `false` | 一个失败的测试观测到了这条完整路径。 |
| `unknown` | 该路径可能相关，但路径证据不完整，或覆盖它的测试超时/执行出错。 |
| `untested` | 选中的测试都没有观测到该路径。机器可读数据保留它，但简明 Markdown 报告不展示它。 |

整个实验流程不使用任何 LLM 服务或 LLM API。

### 结果目录

每个阶段写入带运行指纹的独立目录，不同配置不会互相覆盖：

```text
results/<dataset-version>/<Project-ID>/<buggy|patched>/runs/<run-fingerprint>/
```

主要结果将 CFG 结构和路径标签分开保存。这借鉴了谱系故障定位数据集将程序组件与测试观测分离的组织思路，但没有引入参考项目的模型、代码或依赖：

| 文件 | 内容 |
| --- | --- |
| `cfg.dot` | 一张完整的 Graphviz 有向图，包含全部 CFG 节点和边，并按方法分组。它是纯文本，生成时不需要安装 Graphviz。 |
| `graph.json` | 同一张完整 CFG 的机器可读形式，不省略任何节点或边。 |
| `paths.csv` | 全部完整路径及其逐测试标签记录，也包含没有被选中测试覆盖的路径。 |
| `report.md` | 简明的人类可读摘要、完整 CFG 文件入口，以及全部 `true`、`false`、`unknown` 路径；不会列出无测试证据的路径。 |
| `experiment.json` | 用于重新生成报告的完整聚合数据，还包含配置、摘要、覆盖集合、诊断信息和测试记录。 |

辅助文件包括 `compile.log`、`graph.log`、`test-manifest.json`、`run-manifest.json`、`tests/*.json.gz`、`test-logs/*.log`，以及 patched 阶段的 `patch-application.json`。它们用于诊断和安全续跑；主要实验结果集中在上面的五个输出文件中。

### 完整 CFG 的值

`graph.json` 包含两个数组：

```json
{
  "nodes": [],
  "edges": []
}
```

每个节点包含：

| 字段 | 值 |
| --- | --- |
| `id` | 全局唯一节点标识，由方法标识与 `ENTRY`、`EXIT` 或 `B3` 这类基本块名称组成。 |
| `method_id` | 完整类名、方法名和 JVM descriptor。 |
| `class_name` | Java 完整类名。 |
| `method_name` | Java/JVM 方法名；构造器使用 `<init>`。 |
| `descriptor` | 包含参数类型和返回类型的 JVM 方法 descriptor。 |
| `block_index` | 从 0 开始的基本块编号；虚拟节点为 `-1`。 |
| `start_instruction`、`end_instruction` | 闭区间形式的字节码指令下标；虚拟节点为 `-1`。 |
| `start_line`、`end_line` | 调试行号信息存在时的源码闭区间；无法取得时为 `-1`。 |
| `instruction_count` | 基本块中的字节码指令数；虚拟节点为 `0`。 |
| `virtual` | 合成的 `ENTRY`/`EXIT` 节点为 `true`，实际可执行基本块为 `false`。 |

每条边包含 `source`、`target` 和 `kind`。`source` 与 `target` 是节点 ID；`kind` 可能是 `entry`、`fallthrough`、`jump`、`switch-case`、`switch-default`、`exit`、`throw` 或字节码 CFG 构建器产生的异常处理边类型。CFG 是方法内图，方法调用不会创建跨方法边。

`cfg.dot` 包含完全相同的节点集和边集。实际基本块标签展示块名、源码行范围和字节码指令范围；合成的 `ENTRY`、`EXIT` 节点使用椭圆形。

### 路径与标签的值

`paths.csv` 每行是一条“路径—测试—标签”记录：

| 列 | 值 |
| --- | --- |
| `path_id` | 根据方法与完整节点序列生成的稳定哈希标识。 |
| `method_id` | 这条路径所属的方法内 CFG。 |
| `whole_path` | 完整有序节点序列，例如 `ENTRY -> B0[L33-34] -> B1[L35] -> EXIT`。 |
| `label` | `true`、`false`、`unknown` 或 `untested`，含义见上表。 |
| `observation` | 完整运行路径为 `observed`；路径相关证据不完整为 `unknown`；没有选中测试覆盖为 `not_observed`。 |
| `test_outcome` | `passed`、`failed`、`timed_out` 或 `error`；没有覆盖测试时为空。 |
| `test_id` | `Class::method` 形式的 Defects4J 测试标识；没有覆盖测试时为空。 |

同一路径可能出现多行，因为多个测试可能覆盖它。同一条路径也可能同时拥有来自不同测试的 `true` 和 `false` 记录。循环会使理论路径集合无限，因此静态路径受 `--max-loop-visits` 与 `--max-paths-per-method` 限制；如果运行时出现了不在有界静态集合中的完整路径，程序仍会把它补入输出。

## 依赖

项目保持少量且不变的依赖集合：

| 依赖 | 用途 |
| --- | --- |
| Python `>=3.11` | 运行实验协调器和报告生成器；运行时代码只使用 Python 标准库。 |
| `uv` | 创建虚拟环境、安装项目、锁定依赖，并执行所有 Python 命令。 |
| Defects4J | checkout 项目、导出元数据、编译项目并执行测试。把它克隆在本仓库内部即可，不需要修改 Defects4J。 |
| Java 11 JDK | 编译 Defects4J 项目和 Java helper。无法自动选择 Java 11 时设置 `PATCH_LABEL_JAVA_HOME`。 |
| Git、Subversion、Perl | Defects4J 所需工具；优先使用 `cpanm`，也支持回退到 `cpan`。 |
| ASM 9.8 | 构建字节码 CFG，并在相同基本块边界插入探针；只在构建 helper JAR 时下载。 |
| JUnit 4.13.2 | 为 helper 发现 JUnit 3/4 叶子测试；实际测试执行仍由 Defects4J 完成。 |
| `pytest` | 仅开发测试使用，由 `uv sync --dev` 安装。 |

除标准库外没有 Python 运行时依赖。生成 `cfg.dot` 也没有增加任何依赖。

## 启动运行

在一个父目录中依次克隆两个仓库，使 Defects4J 位于 `patch-label/tools/defects4j`：

```bash
git clone https://github.com/okcomputer2000/patch-label.git
cd patch-label
git clone https://github.com/rjust/defects4j.git tools/defects4j
uv sync --dev
uv run patch-label doctor
uv run patch-label init-defects4j
uv run patch-label build-helper
```

如果 Defects4J 已经克隆并初始化，不要重复克隆，也不需要修改它。Perl 模块已经安装时，可以跳过该步骤：

```bash
uv run patch-label init-defects4j --skip-perl-deps
```

用 5 个测试做冒烟实验：

```bash
uv run patch-label run \
  --example D4JV2.0/Compress-44 \
  --phase both \
  --test-scope relevant \
  --max-tests 5
```

对一个样例的两个阶段执行全部测试：

```bash
uv run patch-label run \
  --example D4JV2.0/Compress-44 \
  --phase both \
  --test-scope all
```

对目录中的全部样例运行完整实验：

```bash
uv run patch-label run --phase both --test-scope all --keep-going
```

使用已有聚合结果重新生成 `report.md`、`cfg.dot` 和 `paths.csv`，不重新执行 Defects4J 测试：

```bash
uv run patch-label report results/.../experiment.json
```

## 命令与参数

所有命令采用以下格式：

```text
uv run patch-label <command> [通用路径参数] [命令参数]
```

每个命令都接受以下通用路径参数：

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--repo-root PATH` | 当前目录 | 仓库根目录，所有相对路径都以它为基准。 |
| `--dataset-dir PATH` | `thinkrepair-patch-diffs` | ThinkRepair patch-diff 数据集目录。 |
| `--defects4j-dir PATH` | `tools/defects4j` | 未修改的 Defects4J clone。 |
| `--state-dir PATH` | `.patch-label` | helper JAR、项目 checkout 和可复用状态目录。 |
| `--output-dir PATH` | `results` | 最终结果根目录。 |

### `doctor`

```text
uv run patch-label doctor [--json] [通用路径参数]
```

检查可执行程序、数据集、Defects4J clone 与初始化状态，以及 Java 11 是否可用。`--json` 以 JSON 输出检查结果；否则输出人类可读列表。必要检查失败时命令返回非零状态。

### `catalog`

```text
uv run patch-label catalog [--output FILE] [通用路径参数]
```

列出发现的全部样例。`--output FILE` 将完整目录写成 JSON，而不是只打印选择器。样例选择器可写为 `Project-ID`，例如 `Compress-44`；也可写为无版本歧义的 `VERSION/Project-ID`，例如 `D4JV2.0/Compress-44`。

### `init-defects4j`

```text
uv run patch-label init-defects4j [--skip-perl-deps] [通用路径参数]
```

初始化已经存在的 Defects4J clone。默认先根据 Defects4J 的 `cpanfile` 安装模块，再运行 `init.sh`。Perl 模块已经可用时，`--skip-perl-deps` 只运行 `init.sh`。

### `build-helper`

```text
uv run patch-label build-helper [--force] [通用路径参数]
```

构建 ASM/JUnit helper 和 Java agent。默认复用指纹一致的缓存 JAR；`--force` 强制重新构建。

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
  [--fresh] [--no-resume] [--keep-going] \
  [通用路径参数]
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--example SELECTOR` | 全部样例 | 选择一个样例；重复该参数可选择多个样例。 |
| `--phase buggy|patched|both` | `both` | 运行原始版本、ThinkRepair 修补版本或两个版本。 |
| `--test-scope all|relevant|trigger` | `all` | 使用全部测试、Defects4J relevant 测试或官方 trigger 测试。完整实验应使用 `all`，后两者用于定向检查。 |
| `--max-tests N` | 不限制 | 只运行发现顺序中的前 `N` 个测试；仅用于冒烟检查，不适合作为最终数据。 |
| `--compile-timeout SECONDS` | `1800` | 项目编译的最长时间。 |
| `--test-timeout SECONDS` | `600` | 每个单独测试的最长时间。 |
| `--discovery-timeout SECONDS` | `600` | 测试方法发现的最长时间。 |
| `--max-loop-visits N` | `2` | 静态枚举的一条路径中，普通 CFG 节点最多出现的次数。 |
| `--max-paths-per-method N` | `1000` | 每个方法最多保存的静态路径数；达到上限会在聚合数据中记录。 |
| `--fresh` | 关闭 | 删除并重建所选运行指纹对应的工具管理结果和 checkout；不会修改 Defects4J 仓库本身。 |
| `--no-resume` | 关闭 | 禁止复用指纹一致的逐测试结果，重新执行所选阶段。 |
| `--keep-going` | 关闭 | 某个样例失败后继续处理后续样例，并写入 `results/failures.json`。 |

### `report`

```text
uv run patch-label report EXPERIMENT_JSON [通用路径参数]
```

读取 schema 2.0 的 `experiment.json`，在同目录重新生成三个易读/导出文件：`report.md`、`cfg.dot` 和 `paths.csv`。该命令不会编译项目、执行测试、应用补丁或调用任何外部 API。
