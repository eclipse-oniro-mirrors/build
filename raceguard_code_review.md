# Raceguard 竞态条件检查 — 编译框架集成代码检视报告

- **检视对象**：`build/templates/cxx/cxx.gni` 中 raceguard 实现（commit `a07725cd` 起的改动）
- **检视环境**：rk3568 全量 `gn gen`（92683 targets / 9728 files），启用态验证采用临时 runtime 替换 + ban 列表隔离
- **结论**：共 10 项问题（2 严重 / 2 高 / 1 中 / 5 低），其中问题 1、3、6、7 已修复提交；**问题 2 + 5 联合构成启用阻塞**，建议改用 sanitizer 式 runtime 链接方案一并解决

## 问题总览

| # | 严重度 | 问题 | 状态 | 提交 |
|---|--------|------|------|------|
| 1 | 严重 | 嵌套模板 `target_name` 遮蔽 → `ohos_cli_executable` 全部 gen 失败 | ✅ 已修复 | `48eb3eca` |
| 2 | 严重 | `hiview:libdatarace_checker_rt` 不存在，启用即 gen 失败 | ❌ 未修复 | — |
| 3 | 高 | static_library / source_set 缺 runtime 依赖，混合场景链接失败 | ✅ 已修复 | `ee9383f5` |
| 4 | 高 | `static_link` 模块被强加共享库依赖，静态链接失败 | ❌ 未修复 | — |
| 5 | 高 | 全系统强加跨部件 external_deps：治理违规 + **结构性依赖环**（实测复现） | ❌ 未修复 | — |
| 6 | 中 | `filter_include` 实参颠倒，ban 列表通配符静默失效 | ✅ 已修复 | `75830e40` |
| 7 | 低 | 丢失 `is_clang` 门控，gcc 工具链下报非法选项 | ✅ 已修复 | `75830e40` |
| 8 | 低 | 插件无存在性校验；路径覆盖为 GN 路径时不 rebase | ❌ 未修复 | — |
| 9 | 低 | JSON 键名无文档，示例文件已删除 | ❌ 未修复 | — |
| 10 | 低 | `product_path` 隐性依赖 musl 的 declare_args | ❌ 未修复 | — |

---

## 已修复

### 问题 1（严重）：嵌套模板 target_name 遮蔽

**现象**：启用 `use_clang_checker` 后 `gn gen` 报 "Unresolved dependencies"，所有 `ohos_cli_executable` 工具失败（ohos-window、ohos-wifiManager、ohos-pasteboard、ohos-storageManager、ohos-audioManager、ohos-avsession-manager、ohos-input、ohos-usageStatsQuery 等 8+）。

**根因**：`ohos_cli_executable` 以改名 target 调用 `ohos_executable("${tool}_exec")`。模板调用块隐式携带外层 `target_name`，经内层 target 块的 `forward_variables_from(invoker, "*")` 转发后**遮蔽** ohos_executable 模板自身的 `target_name`（GN 沙箱复现：inner-scope 读到 `foo_exec`，in-target 读到 `foo`）。于是 config 声明为 `xxx_exec_raceguard_config`，`configs +=` 却引用被遮蔽的 `:xxx_raceguard_config` → 悬空标签。

**修复**：在模板作用域 `if (_raceguard_apply)` 块内将派生名捕获进 `_raceguard_config_target`，声明与引用统一使用该变量（同文件 `_sanitize_config_target` 的既有安全模式）。4 个模板统一。
注意：捕获必须放在 `if (_raceguard_apply)` 块**内**，否则在 `_raceguard_apply=false` 的作用域（host 工具链、被 ban 部件）触发 GN "Assignment had no effect"。

**验证**：修复前同配置必挂；修复后 gen 成功（92683 targets），cli 工具 ninja 均含 `-fpass-plugin=OurCheckerPass.so`，带 flag 文件 11849 → 11871。

### 问题 3（高）：static_library / source_set 缺 runtime 依赖

**机制**：static_library 只做 `ar` 归档、source_set 只编译 `.o`，**自身不链接**；但它们的 deps（含 external_deps 转换结果）会传播到所有消费者的最终链接行（GN 核心语义，OHOS 定制 GN 沙箱实证：`build ./e_static: link obj/e.o ./a.a ./librt.so`）。

**风险场景**：插桩 `.a`/`.o` 中的 runtime 符号引用推迟到最终链接解析。被**未插桩**的最终链接目标（被 ban 部件的 exe/so、host 工具链产物）消费时 → undefined symbol（OHOS 链接带 `-Wl,-z,defs`）。消费者自身也被插桩时无问题（自带依赖）。

**修复**：`ohos_static_library` / `ohos_source_set` 补上与 executable/shared_library 一致的 `external_deps += hiview:libdatarace_checker_rt`，4 个模板统一。

**验证**（ban `[graphic, thirdparty, startup]` + 临时替换 runtime 为零依赖 innerapi `cJSON:cjson` 构造混合场景）：
- 插桩静态库 `lume_engine_dynamic_src` → 未插桩消费者 `libAGPDLL`（graphic 被 ban）：链接行出现 `thirdparty/cJSON/libcjson.z.so`（基线不存在）
- 插桩 source_set `lume_render_src_plugin` → 未插桩消费者 `libPluginAGPRender`：同样出现
- 默认路径回归：gen 成功、0 flag

### 问题 6（中）：filter_include 实参颠倒

**现象**：`filter_include(_raceguard_ban_subsystem_name, [ subsystem_name ])` 实参反了（GN 语义为 `filter_include(values, patterns)`）。字面量名字下两个方向等价；ban 列表含 `*` 通配符时模式匹配方向反转，**静默失效** —— 通配符 ban 的部件仍被检查。

**修复**：4 个模板改回 `filter_include([ subsystem_name ], _raceguard_ban_subsystem_name)`。

**验证**：
- 沙箱实证：旧顺序 `filter_include(["graph*"], ["graphic"])` 不匹配；新顺序匹配
- 端到端（ban `graph*`）：`librender_service`（graphic）无 flag —— 通配符 ban 生效
- 字面量回归（ban part `init`）：`libbegetutil` 无 flag；未 ban 模块正常带 flag

### 问题 7（低）：丢失 is_clang 门控

**现象**：`if (use_clang_checker && is_ohos)` 未含 `is_clang`。`-fpass-plugin` 为 clang 专属选项，gcc 工具链（若启用）下每个编译单元报 unrecognized option。

**修复**：5 处门控（顶层配置读取 + 4 个模板 `_raceguard_apply`）统一补 `&& is_clang`。

**验证**：标准系统恒为 clang，无法直接构造 gcc 场景（防御性修复）；启用态全量 gen（92683 targets）与默认回归（0 flag）通过。

---

## 未修复

### 问题 2（严重）：runtime 库不存在

启用后第一个受检模块即报：

```
ERROR: OHOS innerapi: (libdatarace_checker_rt) not found for component (hiview)
```

全树无此目标。即使补建，还需同时满足：hiview 的 `bundle.json` `inner_kits` 声明、依赖环规避（见问题 5）、产品包含 hiview 部件。**建议与问题 5 一并改方案解决，而不是单独补库。**

### 问题 4（高）：static_link 模块被强加共享库依赖

`static_link = true` 的模块（developtools/hiperf、developtools/hdc 等）以 `-static` 链接，追加共享库 external_dep 直接链接失败。建议 static_link 模块跳过 runtime 依赖（或整个 checker）。

### 问题 5（高）：跨部件 external_deps 的结构性风险（验证后升级）

原有问题：不含 hiview 部件的产品 gen 直接失败；deps_guard / bundle.json 部件依赖治理大面积违规；third_party 模块（musl libc-test 实测会被插桩）引入 OHOS 内部部件，开源合规有风险。

**问题 3 验证过程中实测复现依赖环两次，风险比原评估更严重**：

1. **runtime 库闭包内的任何被插桩模块都会成环**：
   `libhisysevent → libhilog → libhilog_source_ohos → libsandboxlog →(新增) libhisysevent`
2. **任何 `ohos_shared_library` 经默认依赖链传递依赖 startup 的 `parameterbase`**：
   `shared_library_deps → common_deps → musl:soft_shared_libs → soft_libc_musl_shared → parameterbase`
   `parameterbase`（startup，被插桩）加上 runtime 依赖即闭环。实测：
   `parameterbase → cjson →(默认deps) … → parameterbase`
3. hiview 自身模块被插桩后加 `hiview:libdatarace_checker_rt` 属同部件 external_deps，触发 `check_external_deps` "dependency within part" 治理报错。

**推论**：runtime 以「共享库 + external_deps」接入，必须 ban 掉 runtime 依赖闭包覆盖的所有子系统（实测至少 hiviewdfx、startup、thirdparty，实际更多）——不具备可操作性。

**建议（强烈）**：改用 sanitizer runtime 式接入——runtime `.so` 与插件一起部署到 clang 工具链目录，cxx.gni 中 `libs += rebase_path(...)` 直链（参考 `libclang_rt_file` 用法），不走 external_deps。一次性解决依赖环、部件治理、产品裁剪三类问题。

### 问题 8（低）：插件校验缺失与路径处理

- 裸文件名机制本身可行（实测 clang RUNPATH `$ORIGIN/../lib` 会解析 `<clang>/lib` 下的裸名）
- 无 gen 期校验：部署错误 `.so` 时每个编译单元报 `unable to load plugin`；部署非 pass 插件 `.so`（无 `llvmGetPassPluginInfo`）会导致 **LLVM ERROR 崩溃**（实测复现）
- `clang_checker_plugin_path` 若被覆盖为 `//xxx` GN 路径，不 rebase，clang 无法加载
- 建议：注释说明取值约束（绝对路径或裸名，部署位置 `<clang>/lib`），必要时启用时校验

### 问题 9（低）：JSON 键名无文档

配置键为 `ban_subsystem_name` / `ban_part_name`，但示例文件（`raceguard_config_example.json`）已删除、注释未写键名，接入方只能读代码。建议补示例或注释。

### 问题 10（低）：product_path 隐性依赖

`cxx.gni:44` 的 `${product_path}` 实测可用：来自 `third_party/musl/musl_config.gni:106` 的 `declare_args` + hb 注入 args.gn 的宿主绝对路径。风险：

- 耦合 musl 内部参数（musl 改动可能破坏此处）
- 绕过 hb 直接 `gn gen`（args.gn 无该参数）时 `product_path` 默认 `""` → 退化为读 `/build/config/raceguard_config.json`（不存在）→ **静默不过滤**

建议改用 `product_build_config.product_path`（自包含，`//` 源绝对路径形式，由 preloader 生成），或至少加注释说明依赖。

---

## 说明项（非缺陷）

| 项 | 说明 |
|----|------|
| `-fpass-plugin=` vs `-fplugin=` | 前者加载 LLVM pass 插件（`llvmGetPassPluginInfo`，IR 级），后者加载 clang 前端插件（`PluginASTAction`）。结合 runtime 库与 `OurCheckerPass` 命名，应为有意的正确选择 |
| per-target config 开销 | 每个受检模块一个 `config()` target（全量约 1.2 万个），增加 GN gen 开销；实测 gen 耗时无显著劣化，但共享 config 或直接 cflags 可消除 |
| 过滤语义 | 与框架部件归因一致：未声明 `part_name` 的 target 归 `build/build_framework`（如 `init_module_engine_sources`、`lume_engine_dynamic_src`）；测试 target 被 `test.gni` 归为 subsystem `tests` / part=测试类型。ban 部件时这两类不会被过滤，需单独加 ban 项 |

---

## 附录

### 关键机制（本次检视实证）

1. **external_deps 校验/转换内建于 OHOS 定制 GN 二进制**（`"OHOS innerapi"` 报错串存在于 gn 二进制，dotfile `ohos_components_support` 启用），对 BUILDCONFIG `target_type_list` 全部类型统一处理
2. **static_library / source_set 的共享库依赖传播至消费者最终链接**（GN 核心语义，沙箱 + 全量 gen 实证）
3. **嵌套模板 target_name 遮蔽**：调用块隐式携带外层 target_name，`forward_variables_from(invoker, "*")` 传入内层 target 块后遮蔽模板 builtin（沙箱实证）
4. **默认依赖链依赖环**：所有 `ohos_shared_library` 传递依赖 `parameterbase`（startup），被插桩即与任意共享 runtime 成环（实测）

### 验证命令参考

```bash
# 默认路径回归
./build.sh --product-name rk3568 --build-only-gn --no-prebuilt-sdk
prebuilts/build-tools/linux-x86/bin/gn gen out/rk3568

# 启用态（问题 2 修复前需临时替换 runtime 依赖为现有 innerapi）
# out/rk3568/args.gn 追加 use_clang_checker = true
prebuilts/build-tools/linux-x86/bin/gn gen out/rk3568

# 结果检查
rg -l -- "-fpass-plugin=OurCheckerPass" out/rk3568/obj | wc -l        # 带 flag 文件数
rg -c 'fpass-plugin' out/rk3568/obj/<target>.ninja                     # 单目标检查
# 过滤配置：vendor/hihope/rk3568/build/config/raceguard_config.json
#   { "ban_subsystem_name": [...], "ban_part_name": [...] }
```

### 提交记录（build 仓，均含 Signed-off-by + Change-Id）

| 提交 | 内容 | 推送状态 |
|------|------|----------|
| `a07725cd` | Raceguard clang checker（原始实现） | rebase 前版本已推 fork（旧哈希 `56a5deb9`） |
| `48eb3eca` | 问题 1 修复：raceguard config label target_name shadowing | rebase 前版本已推 fork（`063ccfa4`） |
| `32e6f3d6` | 删除孤儿脚本 `prebuilts_tools_filter.py`（无引用） | 未推送 |
| `ee9383f5` | 问题 3 修复：static_library / source_set runtime 依赖 | 未推送 |
| `75830e40` | 问题 6、7 修复：filter_include 实参顺序 + is_clang 门控 | 未推送 |
| `7a092bfd` | 本检视报告 | 未推送 |

> 注：分支经过一次 rebase（原始实现与问题 3 修复的哈希被改写、提交顺序调整），
> fork `blueyouth/master` 仍持有 rebase 前旧链（2 个提交），下次推送需 force。

本地 master 领先 `gitcode/master` 7 个提交（含 `da01d9f6` prebuilts config pruning）。
