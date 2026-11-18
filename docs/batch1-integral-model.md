# 第一批：字符与整数转换

本分支从 `1e91172`（e47ecff 加源码目录修复）开始。根因未修复、边界不完整、过度修复的分类函数及其质量检查、路径匹配函数保持不变。

## 已实现

- Java 字符常量：普通单个 UTF-16 单元、普通转义、合法八进制转义。`'0'` 是 48，`'\0'` 是 0；`'\400'` 不当作合法单字符处理。
- cast 明确读取 tree-sitter 的 `value` 和 `type` 字段，不再把 `(long) x` 的第一个子节点 `long` 当作被转换的值。
- 对由已知整数/字符常量组成的表达式，保留 Java 类型，正确执行扩大、缩位、符号解释及嵌套转换。
- 新字符/cast 所处的闭合整数运算遵守 Java 的数值提升、32/64 位溢出、除法向零取整及余数符号。比如 `(char)65535 * 65535` 是 -131071。
- 新字符/cast 的闭合比较使用同一整数规则；不会把 `0xffffffff` 错当作正的 4294967295。

外部 `_java_expression` 接口仍返回 Z3 表达式，新增的 `IntegralValue` 只在内部常量计算中使用，避免把整个表达式系统重写混入第一批。

## 明确保留的未知

第一批没有建立变量赋值、对象状态、函数返回、数组元素和浮点运算的完整模型。它们不能因为最终存在整数 cast 就被当成已理解的整数值。原始 Unicode 源码转义也暂不接收。

含新字符/cast 的动态比较和动态算术，整体回退为未知表达式，并保留具体依赖说明。不能只加一条“未支持”备注，然后继续用可能错误的整数公式排除路径。例如：

- `char c` 不可能小于 `\0`，不能用允许负数的普通整数代替后声称精确。
- `double` 可能是 NaN，不能用整数三种大小关系把实际可执行情况判成无解。
- 两次同名函数调用可能返回不同值。新未知量按源码内容、语法位置和出现次数区分，不假定函数没有状态变化。

这些保护没有放宽三类确认规则，也不保证消除一题全部 `inconclusive`。Closure-38/51 的实际例子是 `double x` 转 `long`，本批修复读取位置后仍保留浮点转换限制。

## 检查与重算

在 Ubuntu、Python 3.12.3、Z3 4.15.4、tree-sitter 0.26.0、tree-sitter-java 0.23.5、JDK 17 下验证：

```bash
PYTHONPATH=src .venv/bin/python -m pytest --ignore=tests/test_java_helper.py
```

本轮 246 项通过。新增 Java 对照程序实际执行 70 个整数表达式和 4 个比较表达式；分析器接口检查只覆盖本批字符/cast 范围。另有动态表达式、NaN、同名状态调用、原有未支持原因保留等检查。未修改的三项 Java helper/插桩集成测试不在这条命令中；它们不等同于这里已实际执行的表达式 Java 对照。

9 个受保护函数与基线语法树一致：`_root_cause_test`、`_boundary_test`、`_overrepair_test`、`_constraint_quality`、`_boundary_constraint_quality`、`_branch_choice`、`_path_signature`、`_outside_signature`、`_path_touches_unit`。

定向重算读取原版原始记录中直接出现字符/cast 未支持项的 10 题：Codec-15、Lang-16、Lang-28、Lang-51、Compress-17、Gson-5、Closure-131、Closure-38、Closure-51、Closure-77。每题仍运行全部三类判定，保留已有 confirmed，不仅挑其中的暂定标签。

原版和新版使用相同输入、相同依赖分别调用仓库的 `analyze_pair`。显式传入两阶段源码目录，逐文件校验输入哈希，结果存入独立目录。此次不运行 Defects4J、不重新采集测试/路径、不修改补丁或旧表格。逐题结果与运行记录随本轮交付保存。
