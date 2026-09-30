# Ubuntu 正式实验环境

正式运行必须使用输入对应的 Defects4J 历史版。`D4JV1.2` 对应 `v1.2.0`，默认使用 Java 7；其中 Math 的 Ant 构建脚本需要 JavaScript 引擎，必须改用 Java 8。`D4JV2.0` 对应 `v2.0.0` 和 Java 8。辅助程序用 JDK 11 或更新版本编译，其中 Java 7 专用 JAR 使用 ASM 5.2。以下步骤用于重新搭建。

## 安装历史版工具

从仓库根目录开始。以下命令适用于 Ubuntu 24.04 的全新环境；已存在的安装不要覆盖。

```bash
sudo apt-get install -y git subversion perl cpanminus unzip curl openjdk-8-jdk openjdk-11-jdk
repo_root="$(pwd)"
git clone https://github.com/rjust/defects4j.git tools/defects4j
git -C tools/defects4j worktree add "$repo_root/tools/defects4j-v1.2" v1.2.0
git -C tools/defects4j worktree add "$repo_root/tools/defects4j-v2.0" v2.0.0
mkdir -p .patch-label/downloads .patch-label/jdks
curl -fL --retry 3 -o .patch-label/downloads/zulu7.tar.gz \
  https://cdn.azul.com/zulu/bin/zulu7.23.0.1-ca-jdk7.0.181-linux_x64.tar.gz
tar -xzf .patch-label/downloads/zulu7.tar.gz -C .patch-label/jdks
```

Defects4J 1.2 原来的项目仓库下载地址现已失效。本环境用 Defects4J 2.0 官方归档中保留的旧源码修订，并逐一核对输入所需的修订号。下载归档并初始化 2.0：

```bash
curl -fL --retry 3 -o .patch-label/downloads/defects4j-repos-v2.zip \
  https://defects4j.org/downloads/defects4j-repos.zip
unzip -tqq .patch-label/downloads/defects4j-repos-v2.zip
mkdir -p .patch-label/downloads/v2-extracted
unzip -q .patch-label/downloads/defects4j-repos-v2.zip \
  'defects4j/project_repos/*' -d .patch-label/downloads/v2-extracted
mv .patch-label/downloads/v2-extracted/defects4j/project_repos/* \
  tools/defects4j-v2.0/project_repos/
ln .patch-label/downloads/defects4j-repos-v2.zip \
  tools/defects4j-v2.0/project_repos/defects4j-repos.zip
touch -d '2020-02-14 23:35:20 GMT' \
  tools/defects4j-v2.0/project_repos/defects4j-repos.zip
(cd tools/defects4j-v2.0 && cpanm --installdeps . && \
  JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64 bash init.sh)
```

为 1.2 接入同一归档中的六个旧项目仓库，并安装其指定的 Major 1.3.2。1.2 的 `init.sh` 仍指向旧的失效地址，因此不要直接执行它。

```bash
for name in jfreechart closure-compiler.git commons-lang.git \
            commons-math.git mockito.git joda-time.git; do
  ln -s "$repo_root/tools/defects4j-v2.0/project_repos/$name" \
    "tools/defects4j-v1.2/project_repos/$name"
done
ln -s "$repo_root/tools/defects4j-v2.0/project_repos/README" \
  tools/defects4j-v1.2/project_repos/README
ln -s "$repo_root/tools/defects4j-v2.0/framework/lib/test_generation" \
  tools/defects4j-v1.2/framework/lib/test_generation
ln -s "$repo_root/tools/defects4j-v2.0/framework/lib/build_systems" \
  tools/defects4j-v1.2/framework/lib/build_systems
curl -fL --retry 3 -o .patch-label/downloads/major-1.3.2_jre7.zip \
  https://mutation-testing.org/downloads/major-1.3.2_jre7.zip
unzip -q .patch-label/downloads/major-1.3.2_jre7.zip -d tools/defects4j-v1.2
cp tools/defects4j-v1.2/major/bin/.ant tools/defects4j-v1.2/major/bin/ant
(cd tools/defects4j-v1.2 && cpanm --installdeps .)
```

## 运行前检查

```bash
uv sync --dev
uv run patch-label doctor
```

`doctor` 必须全部显示 `OK`，包括 `205/205` 个缺陷编号及其修复前、修复后修订号。它不会证明 205 个补丁都能应用、编译和通过全量测试；这些结论只能由正式实验给出。

正式运行命令如下。它自动选择对应的历史版 Defects4J，可按相同运行指纹恢复中断的逐测试结果；`--keep-going` 使单个样例失败时继续处理其余样例，并在最后返回失败状态。

```bash
uv run patch-label run --phase both --test-scope all --keep-going
```

现有 `labels` 表示路径是否被轨迹观测到，以及覆盖它的**整个测试**是否通过。不完整调用会保留 `observation=incomplete`，但使用测试本身的通过/失败结果：预期异常且测试通过标为 `true`，非预期异常且测试失败标为 `false`。`unknown` 只表示超时、执行错误或没有明确结果；静态路径枚举截断仍会在结果中明确保留。TP/TN/FP/FN 的最终定义须先固定，之后才能生成不误导人的汇总表。
