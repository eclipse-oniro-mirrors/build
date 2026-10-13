# Raceguard 竞态条件检查 — 编译框架集成代码检视报告

- **检视对象**：`build/templates/cxx/cxx.gni` 中 raceguard 实现 + `raceguard_test_plugin/` 插件工具链（commit `a07725cd` 起到 `0ab93250`）
- **检视环境**：rk3568 全量 `gn gen`（当前 141164 targets / 18171 files；首轮 92683 / 9728），启用态验证采用临时 runtime 替换 + ban 列表隔离
- **结论**：共 15 项问题（2 严重 / 3 高 / 2 中 / 8 低）+ 1 项说明（#16）。已修复 1、3、6、7；部分修复 8；未修复 2、4、5、9–15

## 问题总览

| # | 严重度 | 问题 | 状态 | 提交 |
|---|--------|------|------|------|
| 1 | 严重 | 嵌套模板 `target_name` 遮蔽 → `ohos_cli_executable` 全部 gen 失败 | ✅ 已修复 | `48eb3eca` |
| 2 | 严重 | `hiview:libdatarace_checker_rt` 不存在，启用即 gen 失败 | ❌ 未修复 | — |
| 3 | 高 | static_library / source_set 缺 runtime 依赖，混合场景链接失败 | ✅ 已修复 | `ee9383f5` |
| 4 | 高 | `static_link` 模块被强加共享库依赖，静态链接失败 | ❌ 未修复 | — |
| 5 | 高 | 全系统强加跨部件 external_deps：治理违规 + **结构性依赖环**（实测复现） | ❌ 未修复 | — |
| 6 | 中 | `filter_include` 实参颠倒，ban 列表通配符静默失效 | ✅ 已修复 | `5903725c` |
| 7 | 低 | 丢失 `is_clang` 门控，gcc 工具链下报非法选项 | ✅ 已修复 | `5903725c` |
| 8 | 低 | 插件无存在性校验；路径覆盖为 GN 路径时不 rebase | 🟡 部分修复 | `0a8c49d7`（路径已 rebase；校验仍缺） |
| 9 | 低 | JSON 键名无文档，示例文件已删除 | ❌ 未修复 | — |
| 10 | 低 | `product_path` 隐性依赖 musl 的 declare_args | ❌ 未修复 | — |
| 11 | 中 | `build.sh` 硬编码维护者个人绝对路径，换机/换用户即失效 | ❌ 未修复 | — |
| 12 | 低 | 新增源文件缺 Huawei Device Co., Ltd. Apache-2.0 版权头 | ❌ 未修复 | — |
| 13 | 低 | 构建产物写回仓库目录且无忽略项，构建后污染 `git status` | ❌ 未修复 | — |
| 14 | 低 | `is_llvm_build` 时插件路径指向 `out/llvm-install/...`（必然不存在） | ❌ 未修复 | — |
| 15 | 低 | 插件名含 `-aarch64` 而实际 `target_cpu="arm"`，命名误导 | ❌ 未修复 | — |
| 16 | 说明 | 插件自带全局 `operator new/delete`，RTLD_GLOBAL 下进入全局符号域 | 说明 | — |

---

## 已修复

### 问题 1（严重）：嵌套模板 target_name 遮蔽

**现象**：启用 `use_raceguard_checker` 后 `gn gen` 报 "Unresolved dependencies"，所有 `ohos_cli_executable` 工具失败（ohos-window、ohos-wifiManager、ohos-pasteboard、ohos-storageManager、ohos-audioManager、ohos-avsession-manager、ohos-input、ohos-usageStatsQuery 等 8+）。

**根因**：`ohos_cli_executable` 以改名 target 调用 `ohos_executable("${tool}_exec")`。模板调用块隐式携带外层 `target_name`，经内层 target 块的 `forward_variables_from(invoker, "*")` 转发后**遮蔽** ohos_executable 模板自身的 `target_name`（GN 沙箱复现：inner-scope 读到 `foo_exec`，in-target 读到 `foo`）。于是 config 声明为 `xxx_exec_raceguard_config`，`configs +=` 却引用被遮蔽的 `:xxx_raceguard_config` → 悬空标签。

**修复**：在模板作用域 `if (_raceguard_enabled)` 块内将派生名捕获进 `_raceguard_config_target`，声明与引用统一使用该变量（同文件 `_sanitize_config_target` 的既有安全模式）。4 个模板统一。
注意：捕获必须放在 `if (_raceguard_enabled)` 块**内**，否则在 `_raceguard_enabled=false` 的作用域（host 工具链、被 ban 部件）触发 GN "Assignment had no effect"。

**验证**：修复前同配置必挂；修复后 gen 成功（首轮 92683 targets），cli 工具 ninja 均含 `-fpass-plugin=`。

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

**现象**：`if (use_raceguard_checker && is_ohos)` 未含 `is_clang`。`-fpass-plugin` 为 clang 专属选项，gcc 工具链（若启用）下每个编译单元报 unrecognized option。

**修复**：5 处门控（顶层配置读取 + 4 个模板 `_raceguard_enabled`）统一补 `&& is_clang`。

**验证**：标准系统恒为 clang，无法直接构造 gcc 场景（防御性修复）；启用态全量 gen 与默认回归（0 flag）通过。

### 命名与调试残留清理（`0ab93250`）

- `use_clang_checker` → `use_raceguard_checker`（declare_args 公共名，趁未推送统一 raceguard 品牌；推送后改名即破坏 API）
- `_clang_raceguard_checker_plugin_path` → `_raceguard_plugin_path`（并入 `_raceguard_` 前缀家族）
- `_raceguard_apply` → `_raceguard_enabled`（4 个模板）
- `exec_script(check_file_exist.py)` → GN 内建 `path_exists()`（去掉 exec_script 依赖；该脚本在 BUILDCONFIG.gn 仍有 2 处使用，不孤立）
- 删除文件级调试 print ×3（其中 2 处在禁用态每次 gen 也会输出）；修正过时注释（原描述已删除的 `clang_checker_plugin_path` 语义）

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

### 问题 8（低）：插件存在性校验仍缺（路径处理已修复）

已修复部分（`0a8c49d7` + `0ab93250`）：插件路径固定为 `_raceguard_plugin_path`（工具链 `plugins/PAC/lib/libclang_rt.raceguard-aarch64.so`，rebase 到 build dir 注入 `-fpass-plugin=`），原 `clang_checker_plugin_path` 参数删除，覆盖/rebase 问题不复存在。

残留：无 gen 期存在性校验——插件部署缺失时每个编译单元报 `unable to load plugin`；部署非 pass 插件 `.so`（无 `llvmGetPassPluginInfo`）会导致 **LLVM ERROR 崩溃**（实测复现）。建议启用时用 `path_exists` 校验工具链内插件路径并 assert。

### 问题 9（低）：JSON 键名无文档

配置键为 `ban_subsystem_name` / `ban_part_name`，但示例文件（`raceguard_config_example.json`）已删除、注释未写键名，接入方只能读代码。建议补示例或注释。

### 问题 10（低）：product_path 隐性依赖

`cxx.gni:46` 的 `${product_path}` 实测可用：来自 `third_party/musl/musl_config.gni:106` 的 `declare_args` + hb 注入 args.gn 的宿主绝对路径。风险：

- 耦合 musl 内部参数（musl 改动可能破坏此处）
- 绕过 hb 直接 `gn gen`（args.gn 无该参数）时 `product_path` 默认 `""` → `path_exists("/build/config/raceguard_config.json")` 为 false → **静默不过滤**（`0ab93250` 起存在性探测改为内建 `path_exists`，退化语义不变）

建议改用 `product_build_config.product_path`（自包含，`//` 源绝对路径形式，由 preloader 生成），或至少加注释说明依赖。

### 问题 11（中）：build.sh 硬编码维护者个人绝对路径

`raceguard_test_plugin/build.sh:5` 的 `LLVM_DIR` 默认值写死 `/home/blueyouth/code/master/prebuilts/...`，换机器/换用户即失效。脚本位于 `build/raceguard_test_plugin/`，源码根即 `$DIR/../..`，可改相对推导。同时 `LLVM_DIR/include/x86_64-unknown-linux-gnu/...` 的 host 三元组目录名与构建机绑定。

### 问题 12（低）：新增源文件缺版权头

`OurCheckerPass.cpp`、`build.sh` 均无 Huawei Device Co., Ltd. Apache-2.0 版权头，与 AGENTS.md 不变量「新增源文件须带版权头（多数文件）」不符。

### 问题 13（低）：构建产物写回仓库目录

`build.sh:14` 将 `.so` 输出到脚本所在目录（build 仓内）；仓库无对应忽略项，构建后 `git status` 出现未跟踪产物。建议输出到 `out/` 或补忽略项。

### 问题 14（低）：is_llvm_build 下插件路径失效

`default_clang_base_path` 在 `is_llvm_build` 时等于 `//out/llvm-install`（`build/config/clang/clang.gni`），`cxx.gni:42` 拼出的 `out/llvm-install/plugins/PAC/lib/...` 必然不存在；该边界组合下因无存在性校验（见问题 8）会静默到编译期才失败。

### 问题 15（低）：插件名 -aarch64 与目标架构不符

插件名 `libclang_rt.raceguard-aarch64.so` 含 `aarch64`，而 rk3568 为 `target_cpu = "arm"`；且 `-fpass-plugin` 加载的是 host 侧 pass 插件、与目标架构无关，命名具误导性（`cxx.gni:42`、`build.sh:6`）。若为复用 PAC 侧既定命名，建议注释说明。

---

## 说明项（非缺陷）

| 项 | 说明 |
|----|------|
| `-fpass-plugin=` vs `-fplugin=` | 前者加载 LLVM pass 插件（`llvmGetPassPluginInfo`，IR 级），后者加载 clang 前端插件（`PluginASTAction`）。结合 runtime 库与插件命名，应为有意的正确选择 |
| per-target config 开销 | 每个受检模块一个 `config()` target（全量约 1.2 万个），增加 GN gen 开销；实测 gen 耗时无显著劣化，但共享 config 或直接 cflags 可消除 |
| 过滤语义 | 与框架部件归因一致：未声明 `part_name` 的 target 归 `build/build_framework`（如 `init_module_engine_sources`、`lume_engine_dynamic_src`）；测试/fuzz target 被 `test.gni` 归为 subsystem `tests`（如 `RSIpcDosSimulator`、各 `*Test`）。ban `graphic` 不会覆盖 graphic 目录下这类目标（实测带 flag），需单独加 ban 项 |
| 启用态 print | `cxx.gni:45` 的 `print("Clang raceguard checker is active now")` 每次 gen 打印两次（不同 toolchain 作用域），保留可接受 |
| cflags 作用于 C/C++ | config 用 `cflags`，同时注入 C 与 C++ 编译单元；pass 插件为 IR 级、语言无关，无害 |
| 插件部署位置 | 插件部署于 `prebuilts/.../llvm/plugins/PAC/lib/`（工具链目录内，repo clean/rebuild 可能清除）——测试用途可接受，正式应由 PAC 交付流程保证 |
| 问题 16：全局 operator new/delete | 插件自带全局 `operator new/delete`（转发 malloc/free，实现自包含加载）；RTLD_GLOBAL 加载下进入全局符号域，clang 已绑定引用不受影响、语义等价，风险极低，建议注释说明意图 |

---

## 附录

### 关键机制（检视实证）

1. **external_deps 校验/转换内建于 OHOS 定制 GN 二进制**（`"OHOS innerapi"` 报错串存在于 gn 二进制，dotfile `ohos_components_support` 启用），对 BUILDCONFIG `target_type_list` 全部类型统一处理
2. **static_library / source_set 的共享库依赖传播至消费者最终链接**（GN 核心语义，沙箱 + 全量 gen 实证）
3. **嵌套模板 target_name 遮蔽**：调用块隐式携带外层 target_name，`forward_variables_from(invoker, "*")` 传入内层 target 块后遮蔽模板 builtin（沙箱实证）
4. **默认依赖链依赖环**：所有 `ohos_shared_library` 传递依赖 `parameterbase`（startup），被插桩即与任意共享 runtime 成环（实测）
5. **pass 插件加载**（插件侧实证）：OHOS 定制 `PassPlugin.h` 用整数 API 版本（`LLVM_PLUGIN_API_VERSION 1`、`uint32_t APIVersion`，非上游字符串宏）；`registerPipelineStartEPCallback` 为头文件内联实现，插件仅需 clang-15 已导出符号与 libc 即可加载；clang 二进制静态链接 LLVM/libc++（无 `libstdc++`/`libLLVM` 动态依赖），RUNPATH `$ORIGIN/../lib`
6. **rebase 相对路径可解析**：`rebase_path(..., root_build_dir)` 生成的 `../../prebuilts/...` 形式在 out 目录下 dlopen 成功；`PipelineStartEP` 在 O0/O2 均触发（实测）

### 验证命令参考

```bash
# 默认路径回归
./build.sh --product-name rk3568 --build-only-gn --no-prebuilt-sdk
prebuilts/build-tools/linux-x86/bin/gn gen out/rk3568

# 启用态（问题 2 未修复，需临时替换 runtime 依赖为现有零依赖 innerapi，如 cJSON:cjson）
# out/rk3568/args.gn 追加 use_raceguard_checker = true
prebuilts/build-tools/linux-x86/bin/gn gen out/rk3568

# 结果检查
rg -l -- 'fpass-plugin=.*libclang_rt\.raceguard' out/rk3568/obj | wc -l   # 带 flag 文件数
rg -c 'fpass-plugin' out/rk3568/obj/<target>.ninja                        # 单目标检查
# 过滤配置：vendor/hihope/rk3568/build/config/raceguard_config.json
#   { "ban_subsystem_name": [...], "ban_part_name": [...] }

# 插件构建/安装
raceguard_test_plugin/build.sh            # 输出 ./libclang_rt.raceguard-aarch64.so
raceguard_test_plugin/build.sh --install  # 安装到 <clang>/plugins/PAC/lib/
```

### 提交记录（build 仓，均含 Signed-off-by + Change-Id）

| 提交 | 内容 | 推送状态 |
|------|------|----------|
| `a07725cd` | Raceguard clang checker（原始实现） | rebase 前版本已推 fork（旧哈希 `56a5deb9`） |
| `48eb3eca` | 问题 1 修复：raceguard config label target_name shadowing | rebase 前版本已推 fork（`063ccfa4`） |
| `32e6f3d6` | 删除孤儿脚本 `prebuilts_tools_filter.py`（无引用） | 未推送 |
| `ee9383f5` | 问题 3 修复：static_library / source_set runtime 依赖 | 未推送 |
| `5903725c` | 问题 6、7 修复：filter_include 实参顺序 + is_clang 门控（本报告后续 amend 合入此提交） | 未推送 |
| `0a8c49d7` | raceguard 测试插件入库 + cxx.gni：插件路径改为工具链 rebase 路径、删除 `clang_checker_plugin_path` 参数（runtime external_deps 保留，问题 2/4/5 仍在） | 未推送 |
| `0ab93250` | 命名与调试残留清理（见「已修复」） | 未推送 |

> 注：分支经过多次 rebase/amend（哈希被改写；报告提交合并进 `5903725c`，
> 插件相关改动 amend 进 `0a8c49d7`）；fork `blueyouth/master` 仍持有旧链，下次推送需 force。

本地 master 领先 `gitcode/master` 8 个提交（含 `da01d9f6` prebuilts config pruning）。
