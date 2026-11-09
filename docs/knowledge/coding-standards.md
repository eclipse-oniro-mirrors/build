# 编码铁律

> 路径约定：源码路径均相对 build 仓根目录。依据：仓内 `AGENTS.md`「约束与边界」「验证闭环」、`docs/编译选项规范.md`、`docs/how-to-add-a-build-parameter.md` 及模板/脚本实现。

## 公共构建 API（最高优先级）

- **禁止**修改 `ohos_*` 模板的名称、参数签名、默认行为或语义，**禁止**直接 import 单个模板文件（如 `templates/cxx/cxx.gni`）；统一走 `import("//build/ohos.gni")`。`templates/` 下模板是全 OS 公共 API，被所有业务仓消费，改签名即破坏全量构建。
- **禁止**重命名或删除 `ohos_var.gni`/`common.gni`/`version.gni` 中已存在的 `declare_args()` 参数名，**禁止**静默修改其默认值。新增参数前先读 `docs/how-to-add-a-build-parameter.md`，并评估其他仓对旧默认值的隐式依赖。
- **禁止**改变 `ohos.gni` 的 import 顺序或条件 import 分支（`support_jsapi`/`is_arkui_x`/`is_host_product` 驱动）——模板注册依赖该顺序。
- **禁止**修改 `version.gni` 的 `api_version`/`sdk_version` 等版本号（当前 `sdk_version="26.1.0.23"`、`api_version="26"`）——版本与发布流程绑定。

## 生成文件管理

- **禁止**手改任何生成文件：`out/{board}/build_configs/**`（loader 生成的部件 BUILD.gn、parts_info）、`*.pydeps`（`print_python_deps.py` 输出）、`build/ohos/generate_part_info.py` 与 `copy_files.py` 的输出、`build/ohos/sbom/generate_sbom.py` 产物、`ohos/notice/` 生成的 Notice 文件。生成器是唯一真源；要改结果只能改生成器或其输入。
- **禁止**把中间产物提交入库；生成文件头部标注 "Generated code, DONOT modify it" 的（如 `ohos_sdk_modules.gni`、indep 生成的 BUILD.gn），一律从源头重新生成。

## GN / 模板开发

- 修改 `templates/**` 时**必须**参考 `templates/<同类型>/` 既有实现风格（`docs/编译选项规范.md`）；模板内新增能力时保持既有属性语义（如 `module_install_dir` 优先于 `relative_install_dir`）。
- **禁止**在 `ohos_executable`/`ohos_shared_library` 等模板实例里自定义 `output_dir`（cxx 模板显式拒绝）。安装分区只能通过 `install_images`（默认 `["system"]`）选择，安装路径用 `module_install_dir`/`relative_install_dir` 表达。
- 跨部件依赖**必须**用 `external_deps = [ "部件名:模块名" ]`，且被依赖模块须在对方 `bundle.json` 的 `inner_kits` 中声明；**禁止**用裸 label 跨部件依赖（`__check` 目标会对照 `compile_standard_whitelist.json` 检查，`is_use_check_deps` 默认开）。
- 模块进镜像三条件缺一不可：有 `part_name`；目标（或其依赖）在部件 `sub_component` 中；部件在产品部件列表中。注意：当前 master 分支 `ohos_executable` 的 `install_enable` 默认值为 `true`（`templates/cxx/cxx.gni`，仅 `is_double_framework` 时为 false），与 README_zh.md/AGENTS.md 中「默认不安装」的历史表述存在差异——对可执行模块显式设置 `install_enable` 仍是稳妥做法，且文档旧表述适用于历史分支。

## Python（hb/脚本）开发

- Python 3.9+ 兼容（hb 要求 >=3.8，仓库无统一 linter）；改动 `.py` 后**必须**至少跑 `python3 -m py_compile <file>` 确认可导入。
- hb 侧新增/修改参数**必须**在 `hb/resources/args/default/*.json` 定义（Arg 体系），由 `hb/resolver/` 相应阶段 resolver 消费；**禁止**在服务里自行解析 `sys.argv`。
- **禁止**绕过 `Config` 单例（`hb/resources/config.py`，持久化 `out/ohos_config.json`）直接读写产品状态；**禁止**绕过 `SystemUtil.ExecEnviron` 的 `compile_env_allowlist.json` 白名单向 ninja 透传宿主环境变量。
- 日志一律走 `LogUtil`；敏感信息（密钥、token 等）不得明文入日志——`sensitive_info_config.json` 的脱敏只作兜底，不作依赖。
- 新增源文件**必须**带版权头（多数文件为 Huawei Device Co., Ltd.，Apache 2.0）。

## 环境与可复现

- **禁止**绕过 `prebuilts_download.sh` 自行替换 prebuilts 工具链版本；Node.js 硬锁 **v14.21.1**（`build_scripts/build.sh` 校验不匹配直接退出）；clang/python/node 版本是构建可复现前提。
- **禁止**为绕过报错而修改 `subsystem_config.json` 把外部路径映射进来——它是全 OS 子系统注册表；产品侧差异用 `out/products_ext/{product}/` 覆盖机制或产品仓 overlay 解决。
- **禁止**把子系统名当 `--build-target`，也不要只传部件名；使用完整形式 `out/{device}/build_configs/{subsystem}/{part}:{part}`；重名目标用 `{path}:{target}`。

## 合规（License 敏感）

- 引入/升级三方依赖**必须**同步更新 Notice 与 OAT（`OAT.xml` 为静态审计配置）；license 解析三级回退：模块模板 `license_file` 参数 → 模块目录 `README.OpenSource` → 沿目录向上递归 LICENSE/NOTICE/COPYRIGHT/COPYING（`ohos/notice/collect_module_notice_file.py`）。
- 任何白名单改动（`*_whitelist.json`、`third_party_allow_list.json`、`compile_*_whitelist.json`）属 license-sensitive，提交前必须请示；白名单优先读 `out/products_ext/{product}/` 产品级覆盖版。
- `--sbom` 生成的 SPDX 产物（`out/{board}/sbom/`）依赖 `gn_gen.json` 与 repo manifest，**禁止**手工编辑或伪造。

## 测试验证（改动后必跑）

- 改 `templates/**`：`bash build/test/script/start_ex.sh template`；改构建参数/选项：`start_ex.sh option`；性能相关：`start_ex.sh performance`；顺序运行用 `start_ex.sh all`（option → performance → template，顺序不可变）。
- 改 hb Python：`build/hb/test/unitTest/` 下 `python3 -m unittest discover`（当前该目录有效断言极少，靠 py_compile + 端到端补位）。
- 端到端：`./build.sh --product-name rk3568 --build-only-gn` 先确认 `gn gen` 不报错，再按需全量构建确认镜像产出。
- 注意：`start_ex.sh` 会 `rm -rf out`、覆盖 `build/common/bundle.json` 并 `repo forall git reset --hard + clean -dfx`，**禁止**在含未提交改动的工作区运行，只能在专用流水线环境执行。
