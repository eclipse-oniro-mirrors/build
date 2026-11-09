# 业务实体知识

build 仓核心业务实体的完整知识定义，涵盖静态概念、动态运行、规则约束与代码映射。> 源码路径均相对 build 仓根目录。

## 产品配置（ProductConfig）

### 实体概念
| 子维度 | 内容 |
|---|---|
| 实体名称 | 产品配置 / vendor 产品 config.json |
| 实体定义 | 描述一个可构建产品（rk3568、ohos-sdk 等）的系统级别、板级与部件组成的数据集合；是构建流程最上游的业务输入 |
| 核心特征 | 字段：`product_name`、`type`（mini/small/standard）、`target_cpu`、`device_company`、`board`、`enable_ramdisk`、`subsystems[].components[].features`；v3 版式支持继承链（product_enable_list/component_exclude_list，解析见 `hb/util/preloader/parse_vendor_product_config.py`） |
| 类型/分类 | 按来源四类：`vendor/{company}/{product}/config.json`、内置 `productdefine/common/products/`、扩展 `out/products_ext/vendor`、`toolchain/llvm-project/llvm_products`（由 `ProductUtil.get_products()` 动态扫描） |

### 上下文与场景
| 子维度 | 内容 |
|---|---|
| 交互流程 | `hb set`（Menu 交互）或 `hb build --product-name` 触发 `set_args_resolver.resolve_product_name` → `ProductUtil.get_device_info()` 提取 board/kernel/target_cpu/target_os → 写 `Config` 单例并持久化 `out/ohos_config.json` → preloader 的 `Product._do_parse()` 完成 syscap/device/parts/build_vars/excluded 更新 |
| 状态流转 | 产品一经 set 即固定于 `out/ohos_config.json`（含 out_path：standard → `out/{board}`、host → `out/host/{product}`、mini/small → `out/{board}/{product}`，产品 `build_out_path` 可覆盖）；`hb clean` 不清除该文件，`hb set` 重选产品时覆盖 |

### 规格与约束
| 子维度 | 内容 |
|---|---|
| 业务规则 | 产品 feature 必须在部件 bundle.json 中有定义，否则 loader 报 OHOSException 2006；产品可用 `subsystem_config_json` 字段指定替代子系统注册表；`out/products_ext/{product}/` 下同名配置优先 |
| 并发约束 | `Config` 为 Singleton（`hb/resources/config.py:39`），进程内全局唯一；构建串行消费，无跨进程共享写 |

### 知识关联
| 子维度 | 内容 |
|---|---|
| 上层依赖 | `Config` 单例持有当前产品（`config.product_json`、`config.board`、`config.target_cpu` 等）；preloader 产物 `out/preloader/{product}/` 以产品命名空间隔离 |
| 下游影响 | 决定 loader 扫描范围（部件列表）、GN args（product_name/device_name/product_path 等，由 `build_args_resolver.resolve_product` 注册）、out 路径、镜像平台展开 |
| 平级关联 | 子系统注册表（overlay 追加）；`component_feature_whitelist.json` 校验产品 features 合法性 |

### 代码与符号
| 子维度 | 内容 |
|---|---|
| 核心类 | `ProductUtil`（`hb/util/product_util.py:30`，metaclass=NoInstance）；`Product`（`hb/util/preloader/preloader_process_data.py`）；`Config`（`hb/resources/config.py:39`，Singleton） |
| 核心方法 | `ProductUtil.get_products()`（枚举四类产品目录）；`ProductUtil.get_device_info()`（`hb/util/product_util.py:172`）；`Product._do_parse()`（含 `_update_parts/_update_build_vars/_remove_excluded_components`） |

## 子系统注册（SubsystemConfig）

### 实体概念
| 子维度 | 内容 |
|---|---|
| 实体名称 | 子系统注册表 / subsystem_config.json |
| 实体定义 | 全源码树共享的「子系统名 → 源码路径列表」映射，圈定 loader 的扫描范围 |
| 核心特征 | JSON 对象：`{子系统名: {path: "路径"[, name: "名称"]}}`，如 `"arkui": {"path": "foundation/arkui", "name": "arkui"}`；示例文件 `subsystem_config_example.json` |
| 类型/分类 | 全局默认（`build/subsystem_config.json`）+ 产品 overlay（`subsystem_config_overlay.json`，仅 vendor/device 路径）+ preloader 合并产品/设备专属子系统后输出 `out/preloader/{product}/subsystem_config.json` |

### 上下文与场景
| 子维度 | 内容 |
|---|---|
| 交互流程 | preloader 合并输出 → loader `subsystem_info.get_subsystem_info()` 读取 → `subsystem_scan.scan` 以 `multiprocessing.Pool` 并行 `os.walk` 各子系统路径，收集所有 `bundle.json`/`ohos.build`，产出 `build_configs/subsystem_info/{subsystem_build_config,src_subsystem_info,no_src_subsystem_info}.json` |
| 状态流转 | 静态注册表 + 每次构建重新扫描；mini/small 系统不读此表，改由 `parse_lite_subsystems_config.py` 解析 `build/lite/components/*.json` |

### 规格与约束
| 子维度 | 内容 |
|---|---|
| 业务规则 | 子系统名不是可构建目标；不得为绕过报错把外部路径映射进来（AGENTS.md「不可破坏」）；loader 用 `subsystem_components_whitelist.json` 做部件名→子系统名归一（如 acts/hats→xts） |
| 并发约束 | 扫描阶段多进程只读；`preserve_target_order` 保持生成目标顺序稳定 |

### 知识关联
| 子维度 | 内容 |
|---|---|
| 上层依赖 | 全局共识文件，由各子系统维护注册项；产品 overlay 可追加 |
| 下游影响 | 决定 loader 扫描的源码目录集合 → 部件发现范围 |
| 平级关联 | 产品配置（subsystem_config_json 字段可替换）；subsystem_info 校验（缺失报 2001-2005） |

### 代码与符号
| 子维度 | 内容 |
|---|---|
| 核心类 | `subsystem_scan` 模块（`hb/util/loader/subsystem_scan.py`）、`subsystem_info`（`hb/util/loader/subsystem_info.py`） |
| 核心方法 | `subsystem_info.get_subsystem_info`；`subsystem_scan.scan`（多进程 walk，max_depth=5，忽略 .git/docs/node_modules） |

## 部件（Component / Part）

### 实体概念
| 子维度 | 内容 |
|---|---|
| 实体名称 | 部件（component，运行实体 part） |
| 实体定义 | 可独立配置与裁剪的功能单元；构建侧由源码仓根 `bundle.json`（或旧式 `ohos.build`）的 `component` 节定义，经 loader 归一为统一的部件构建对象 |
| 核心特征 | 关键子节：`name`、`subsystem`、`syscap`、`features`、`deps`、`build.sub_component`（构建入口）、`build.inner_kits`（跨部件接口）、`build.test`（测试目标）、`build.conditions`（条件编译：按 compile_mode/target_os/target_cpu/os_level 匹配）；归一后属性：module_list/inner_kits/test_list/feature_list/system_capabilities |
| 类型/分类 | 普通部件、三方部件（`third_party_allow_list.json` 判定）、测试部件（subsystem=tests）、产品/设备专属部件（`product_{name}`、`device_{board}`） |

### 上下文与场景
| 子维度 | 内容 |
|---|---|
| 交互流程 | loader 扫描发现 bundle.json → `BundlePartObj` 装载（`__init__` 读 exclusion/dependency_pruning 配置并构建条件上下文）→ `to_ohos_build()`（`hb/util/loader/load_bundle_file.py:199`）按条件编译上下文归一为 ohos.build 形态 → `PartObject` 为每部件×variant 生成 `build_configs/{subsystem}/{part}/BUILD.gn`（模板 `ohos_part/ohos_inner_kits/ohos_system_kits/ohos_part_test`） |
| 状态流转 | 生成态：bundle.json（声明）→ parts_info.json / part_subsystem.json / components.json 等落盘（`build_configs/parts_info/`）；vendor/device 部件可用 `component.override` 整部件替换并回写 parts.json（`_override_components`） |

### 规格与约束
| 子维度 | 内容 |
|---|---|
| 业务规则 | 部件名与子系统归属需一致（loader 校验，白名单豁免见 `compile_standard_whitelist.json`）；`--build-target` 用完整形式 `out/{device}/build_configs/{subsystem}/{part}:{part}`；部件能否进镜像取决于是否在产品部件列表（target_platforms_parts）；独立编译部件受 `indep_component_whitelist.json` 约束 |
| 并发约束 | 扫描阶段多进程只读；生成阶段单进程写；`component_compilation_whitelist.json` 豁免 innerapi_not_lib 等编译规范检查 |

### 知识关联
| 子维度 | 内容 |
|---|---|
| 上层依赖 | 产品配置决定部件取舍与 feature 取值；子系统注册表决定扫描路径 |
| 下游影响 | 部件 BUILD.gn 聚合模块目标；parts_info 被 external_deps 解析（inner_kits_info.json）与打包（system_install_parts.json）消费；`auto_install_whitelist.json` 允许部分部件免声明自动安装 |
| 平级关联 | 部件间依赖：源码侧 bundle.json `deps.components`，构建侧模块 external_deps；`component_override_map` 支持部件替换映射 |

### 代码与符号
| 子维度 | 内容 |
|---|---|
| 核心类 | `BundlePartObj`（`hb/util/loader/load_bundle_file.py:29`）、`PartObject`（`hb/util/loader/load_ohos_build.py:108`） |
| 核心方法 | `BundlePartObj.to_ohos_build()`；`load_ohos_build.get_parts_info`；`generate_targets_gn.gen_targets_gn/gen_phony_targets/gen_stub_targets` |

## 模块（Module / module_info）

### 实体概念
| 子维度 | 内容 |
|---|---|
| 实体名称 | 模块（ohos_* 模板目标） |
| 实体定义 | 部件内一个可编译安装的构建单元，由业务仓 BUILD.gn 中的 `ohos_*` 模板实例定义；构建期派生出 module_info 元数据描述其安装行为 |
| 核心特征 | module_info.json 字段：label、module_name、source、dest（列表）、import_desc、notice、version_script、syscap、test_type 等；dest 由 `gen_install_dests()` 按分区 base_dir 计算 |
| 类型/分类 | 按模板：`ohos_shared_library`/`ohos_executable`/`ohos_static_library`/`ohos_source_set`/`ohos_prebuilt_*`/`ohos_rust_*`/`ohos_hap`/`ohos_ndk_library` 等；按用途：产品模块与测试模块（testonly，`subsystem_name="tests"`） |

### 上下文与场景
| 子维度 | 内容 |
|---|---|
| 交互流程 | GN 求值模板 → `generate_module_info`（metadata 模板）exec_script 调 `gen_module_info.py` 产 `{module}_module_info.json` → `metadata.install_modules` 经 generated_file 聚合为 `*_install_info.json` → 打包阶段 `modules_install.py` 按 dest 拷入分区文件树 |
| 状态流转 | 声明（BUILD.gn）→ 元数据（module_info.json）→ 聚合（install_info）→ 落盘（packages/{platform}/{image}/）；`install_enable=false` 的模块在 `copy_modules` 中被跳过 |

### 规格与约束
| 子维度 | 内容 |
|---|---|
| 业务规则 | 进镜像三条件：有 part_name、在部件 sub_component（或被依赖）、部件在产品列表；安装路径优先级 `module_install_dir` > `relative_install_dir` > `base/{module_type}`；innerapi so 的安装路径受 `ohos/images/get_module_install_dest.py` 校验（allowed-lib-list）；`ohos_executable` install_enable 当前默认 true（差异说明见 [coding-standards.md](coding-standards.md)） |
| 并发约束 | GN/ninja 天然并行调度，模块产物无跨模块写冲突；module_info 由 exec_script 生成，属生成物不可手改 |

### 知识关联
| 子维度 | 内容 |
|---|---|
| 上层依赖 | 部件持有模块（sub_component）；模板决定元数据生成规则 |
| 下游影响 | modules_install 消费 dest 落盘；Notice 收集以模块为单位；`innerapi_tags` 决定 so 分发目录（ndk/platformsdk/chipsetsdk） |
| 平级关联 | 模块间依赖：部件内 deps（label）与跨部件 external_deps（部件名:模块名） |

### 代码与符号
| 子维度 | 内容 |
|---|---|
| 核心类 | `templates/metadata/module_info.gni`（generate_module_info 模板）、`templates/cxx/cxx.gni`（可执行/库模板） |
| 核心方法 | `gen_install_dests()`（`templates/metadata/gen_module_info.py:62`）、`gen_module_info()`（同文件 :148）；`copy_modules()`（`ohos/packages/modules_install.py:68`） |

## 构建参数（BuildArgs / declare_args）

### 实体概念
| 子维度 | 内容 |
|---|---|
| 实体名称 | GN 构建参数（declare_args）与 hb 参数 |
| 实体定义 | 控制构建行为的键值集合；GN 侧由 `declare_args()` 声明，hb 侧由 args json 定义并转换为 `--gn-args`/`--ninja-args` |
| 核心特征 | GN 分层：`common.gni`（build_variant 等 4 个）、`version.gni`（sdk_version/api_version 等版本组）、`ohos_var.gni`（目录基址/SDK 开关/行为开关 40+ 组）、`config/BUILDCONFIG.gn`（产品级：product_name、is_standard_system、support_jsapi、build_xts 等）；hb 参数定义在 `hb/resources/args/default/*.json`，当前值持久化 `out/hb_args/*.json` |
| 类型/分类 | 产品派生参数（preloader build_vars/部件 features→build_gnargs.prop）、用户参数（--gn-args）、框架默认参数 |

### 上下文与场景
| 子维度 | 内容 |
|---|---|
| 交互流程 | `hb build` 解析参数 → `build_args_resolver.resolve_product` 把产品信息 regist_arg 为 GN args → `gn gen --args="key=value ..."` 消费 → `Gn._convert_args` 拼串；部件 features 由 preloader 写 build_gnargs.prop 后进入 args |
| 状态流转 | 声明（gni/json）→ 命令行覆盖（--gn-args）→ 落盘 `out/{board}/args.gn`；`--clean-args` 默认 True 编完清理 hb_args |

### 规格与约束
| 子维度 | 内容 |
|---|---|
| 业务规则 | 已有参数名不可改名/删除，默认值不可静默改（AGENTS.md「不可破坏」）；新增参数须读 `docs/how-to-add-a-build-parameter.md`；`version.gni` 版本号与发布流程绑定 |
| 并发约束 | args.gn 单写者（gn gen）；hb_args json 由 Arg 体系管理，构建结束清理 |

### 知识关联
| 子维度 | 内容 |
|---|---|
| 上层依赖 | 产品配置与部件 features 派生参数值 |
| 下游影响 | 全部模板行为（安装目录、sanitize、SDK 开关）、打包与镜像选项 |
| 平级关联 | `component_feature_whitelist.json` 约束部件可用 feature 名 |

### 代码与符号
| 子维度 | 内容 |
|---|---|
| 核心类 | `Arg`/`BuildPhase`（`hb/containers/arg.py`）、`BuildArgsResolver`（`hb/resolver/build_args_resolver.py`） |
| 核心方法 | `BuildArgsResolver.resolve_product`/`resolve_build_target`/`resolve_gn_args`；`Gn._convert_args`（`hb/services/gn.py`） |

## 分区镜像（Image）

### 实体概念
| 子维度 | 内容 |
|---|---|
| 实体名称 | 分区镜像（system/vendor/userdata/... .img） |
| 实体定义 | 最终可烧录的系统版本产物：把分区文件树按文件系统配置封装为镜像文件 |
| 核心特征 | 镜像清单（`ohos/images/BUILD.gn`）：system、vendor、userdata、sys_prod、chip_prod、updater_ramdisk、eng_system、eng_chipset（+ramdisk）；chip_prod 按 chip_product_list 多产品展开；参数来自 `mkimage/{image}_image_conf.txt`（第 1 行挂载点、第 2 行大小、其后 flags） |
| 类型/分类 | ext4（system 2GiB/vendor 256MiB/sys_prod 50MiB/eng_* 12MiB）、f2fs（userdata 1.4GiB，--prjquota）、cpio（ramdisk 4.5MiB） |

### 上下文与场景
| 子维度 | 内容 |
|---|---|
| 交互流程 | ninja 目标 `images` → 每平台 `{platform}_{image}_image` action（前置 deps：`{platform}_install_modules`、process_field_validate、check_seccomp）→ `build_image.py` 准备分区脚手架（root 的 bin/etc/lib 软链 /system；userdata 先清空重建；eng_system 在 `build_variant=user` 时置空）→ `mkimages.mk_images` 按类型分发 mk 工具 → `out/{device}/packages/phone/images/{image}.img` |
| 状态流转 | 分区文件树（packages/{platform}/）→ 镜像（.img）；设备可用 `packages/{product}/imagesconf/{image}_image_conf.txt` 覆盖默认配置；debug/asan 场景用 `mkimage/debug|asan/` 覆盖 |

### 规格与约束
| 子维度 | 内容 |
|---|---|
| 业务规则 | DAC（`mkimage/dac.txt` 定义 mode-uid-gid-cap）与 SELinux file_contexts 必须配套；host 产品走 `host_images`；mini/small 不走本机制（lite 用 `fs_process.py` packer） |
| 并发约束 | 各镜像 action 相互独立可并行，但统一前置 install_modules |
| 安全约束 | `process_field_validate.py` 高权限/关键进程白名单校验先行；chip_ckm 镜像显式依赖 selinux_adapter:build_contexts |

### 知识关联
| 子维度 | 内容 |
|---|---|
| 上层依赖 | 分区文件树（模块 dest 落盘结果）；`target_platforms_parts.json`（平台×部件） |
| 下游影响 | 版本发布与烧录；postbuild 的 rom-size-statistics/archive-image 统计与归档 |
| 平级关联 | e2fsprogs/f2fs-tools host 工具链（镜像 action 显式 deps） |

### 代码与符号
| 子维度 | 内容 |
|---|---|
| 核心类 | `build_image.py` 的准备函数族；`mkimage/mkimages.py`（load_config/mk_system_img） |
| 核心方法 | `_prepare_root()`（`ohos/images/build_image.py:36`）、`_prepare_ramdisk()`（:81）、`_make_image()`（:127）；`mkextimage.py`/`mkf2fsimage.py`/`mkcpioimage.py` |

## SDK / NDK 包

### 实体概念
| 子维度 | 内容 |
|---|---|
| 实体名称 | SDK 包与 NDK 包 |
| 实体定义 | 面向应用/南向开发者的交付物：把指定模块产物按描述清单组织成多平台 zip 包（含 oh-uni-package.json 与 Notice） |
| 核心特征 | SDK 由描述文件驱动：`ohos/sdk/ohos_sdk_description_std.json`（205 项：install_dir + module_label + target_os[]，可被 ext_sdk_config_file 覆盖）；NDK 目标清单来自 `//interface/sdk_c/ndk_targets.gni`；产物命名 `{type}-{os}-{arch}-{version}.zip` |
| 类型/分类 | 按目标平台 linux/windows/darwin/ohos/linux_arm64（工具链映射：windows=mingw_x86_64、ohos=ohos_clang_arm64）；按类型 ts/js/native（display 名见 `type_to_display_name.json`） |

### 上下文与场景
| 子维度 | 内容 |
|---|---|
| 交互流程 | 产品 `ohos-sdk` 构建时根 BUILD.gn 切换为 `build_ohos_sdk`（ndk+sdk+verify）→ gn 阶段 exec_script 链（generate_hap_build_sdk_config → parse_description → parse_sdk_description）产 `ohos_sdk_modules.gni` → `make_sdk_modules`（copy_and_archive → copy_sdk_modules.py 按 module_info source 拷贝并 zip）→ check_sdk_completeness（对照 sdk_delivery_list）→ 可选签名（sign_sdk.py） |
| 状态流转 | 产物 `out/sdk/packages/ohos-sdk/{os}/`；`use_current_sdk` 分支改写到 `{class}-current-sdk/{os}/{api_version}`；Notice 经 `add_notice_file.py` 写入归档目录 |

### 规格与约束
| 子维度 | 内容 |
|---|---|
| 业务规则 | 交付内容受 `sdk_delivery_list.json` 清单约束并做完备性校验；`sdk_build_public/arkts` 开关影响对外面；NDK 头文件经 `check_ndk_header.py`+`check_ndk_header_signature.py` 双检查（按 sdk_build_check_level/public 过滤） |
| 并发约束 | 描述解析在 gn 阶段串行；各平台包构建并行 |
| 安全约束 | 签名默认关闭（enable_sign 控制）；kits so 删除检查（`ohos/kits/kits_check.gni`，签名目录 interface/innersdk/native） |

### 知识关联
| 子维度 | 内容 |
|---|---|
| 上层依赖 | 模块产物与 module_info；版本号（version.gni 派生 current_sdk_version/current_ndk_version） |
| 下游影响 | 开发者工具链（NDK 含 cmake 工具链文件 OHOS.cmake/ohos.toolchain.cmake） |
| 平级关联 | `parse_sdk_description.py` 的 `variant_to_product.json`；cangjie SDK 由 `remove_cangjie_ohos_sdk_config.py` 剔除 |

### 代码与符号
| 子维度 | 内容 |
|---|---|
| 核心类 | `ohos/sdk/sdk.gni`（make_sdk_modules 等模板）、`ohos/ndk/ndk.gni`（ohos_ndk_* 模板族） |
| 核心方法 | `parse_sdk_description.parse_description_file/write_sdk_build_gni/expand_platform_targets`；`copy_sdk_modules.py`；`archive_ndk.py` |

## 开源合规产物（Notice / SBOM）

### 实体概念
| 子维度 | 内容 |
|---|---|
| 实体名称 | Notice 文件族与 SBOM 清单 |
| 实体定义 | 开源合规交付物：Notice 声明镜像内开源软件许可；SBOM 产出 SPDX 2.3 格式的软件物料清单 |
| 核心特征 | Notice：模块级 `.notice.txt`(+json) → `NOTICE_FILES/` → 合并 `NOTICE.txt` + `NOTICE.xml.gz` + `NOTICE_module_info.json`（进 system/etc 或 vendor/etc）；SBOM：`{out}/sbom/sbom_meta_data.json` + `spdx.json`（SPDX 2.3） |
| 类型/分类 | 模块级收集 / 系统级合并 / NDK/SDK 级（merge_ndk_notice、generate_sdk_notice） |

### 上下文与场景
| 子维度 | 内容 |
|---|---|
| 交互流程 | Notice：每模块自动挂 `collect_notice` action（license 三级回退：license_file 参数 → README.OpenSource → 向上递归 LICENSE/NOTICE/COPYRIGHT/COPYING）→ packages/BUILD.gn `collect_notice_files`(zip) → `merge_notice_files`（按内容 hash 去重）→ `core/build_scripts/verify_notice.sh` 分区行数校验。SBOM：`hb build --sbom`（postTargetGenerate 阶段异步）→ generate_sbom.py 生成 manifest → SBOMGenerator（InstallModuleAnalyzer+DependGraphAnalyzer）→ spdx23 转换 |
| 状态流转 | 模块收集 → 分区聚合 → 合并去重 → 校验入镜像；SBOM 读 `out/sbom/gn_gen.json`（--json-file-name 注入）与 repo manifest，产出后清理临时文件 |

### 规格与约束
| 子维度 | 内容 |
|---|---|
| 业务规则 | 引入/升级三方依赖必须同步更新 Notice 与 OAT（AGENTS.md「合规闭环」不变量）；NOTICE.txt 与 NOTICE.xml.gz 同源（hash 去重）；`verify_notice.sh` 失败即构建失败 |
| 并发约束 | 模块级 notice 收集随 ninja 并行；合并与校验在打包阶段串行；SBOM 在独立线程异步执行 |
| 安全约束 | OAT.xml（仓根）为独立静态审计配置，不在 ninja 流水线内；SBOM 数据不得手工编辑 |

### 知识关联
| 子维度 | 内容 |
|---|---|
| 上层依赖 | 模块 module_info（模块→部件→dest 链）；`enable_notice_collection`（ohos_var.gni，默认 true） |
| 下游影响 | 发布合规审查；SBOM 的文件/包/文档三层信息（pipeline/sbom_generator.py） |
| 平级关联 | `merge_notice_files.py` 与 `ohos/sbom/extraction/copyright_and_license_scanner.py` 共享版权扫描语义 |

### 代码与符号
| 子维度 | 内容 |
|---|---|
| 核心类 | `ohos/notice/notice.gni`（collect_notice 模板）；`ohos/sbom/pipeline/sbom_generator.py`（SBOMGenerator.build_sbom）；`ohos/sbom/converters/spdx23.py` |
| 核心方法 | `do_collect_notice_files()`（`ohos/notice/collect_module_notice_file.py`）；`do_merge_notice_files()`/`compute_hash()`（`ohos/notice/merge_notice_files.py`）；`generate_manifest()`/`generate_sbom()`（`ohos/sbom/generate_sbom.py:38/92`） |

## 预编译工具链（Prebuilts）

### 实体概念
| 子维度 | 内容 |
|---|---|
| 实体名称 | 预编译工具链（prebuilts） |
| 实体定义 | 构建所需的宿主工具与运行时（node/cmake/gn/ninja/clang/python/rust/hvigor/cangjie/hpm 等）的受版本控制集合 |
| 核心特征 | `prebuilts_config.json` 声明 29 类工具：download_root + tool_list（每项按平台×CPU 配 remote_url/unzip_dir/unzip_filename，含 tag/build_type/build_level 过滤与配置继承：平台>工具>全局） |
| 类型/分类 | src 全量（下载到 `../openharmony_prebuilts`，源码树旁）与 indep 独立编译（下载到 `~/.prebuilts_cache`，按部件 tag 裁剪，恒加 "base"） |

### 上下文与场景
| 子维度 | 内容 |
|---|---|
| 交互流程 | `prebuilts_download.sh`（全量：建 oh_venv + pip 装依赖 → 调 `prebuilts_config.py` → L0/L1 精简 vs 标准集 pip 包 → 可选 --download-sdk → 整理 llvm libcxx-ndk/合成 llvm_ndk）；独立编译预下载 `prebuilts_config.sh --download-sdk`；hb indep_build 前 `services/prebuilts.py` 按 `prebuilts/.local_data/last_update.json` + 配置 mtime 判断是否跳过 |
| 状态流转 | 声明（json）→ 下载（sha256 内存缓存+锁）→ 后处理（symlink/copy/remove/move/shell 五类 handle）→ 工具就位（gn/ninja 从 `prebuilts/build-tools/{linux-x86|linux-aarch64}/bin` 解析） |

### 规格与约束
| 子维度 | 内容 |
|---|---|
| 业务规则 | Node.js 硬锁 v14.21.1（build.sh 校验，不匹配退出）；不得绕过下载脚本自行替换工具链版本（可复现前提）；下载失败 hpm build 自动重试 3 次 |
| 并发约束 | `PoolDownloader` 线程池并发下载；sha256 校验带内存缓存与锁，避免重复下载 |
| 安全约束 | remote_url+sha256 校验；npm/ohpm 源固定（华为云/harmonyos） |

### 知识关联
| 子维度 | 内容 |
|---|---|
| 上层依赖 | 部件依赖（indep 模式按 `part_prebuilts_config.json` 部件→tag 映射裁剪） |
| 下游影响 | 编译执行层（clang/rustc/node 实际可执行文件）；hb 的 Gn/Ninja 服务从中解析二进制路径（找不到抛 '0001'） |
| 平级关联 | `build_scripts/build.sh` 的环境检查（tools_checker.py dpkg 依赖）；oh_venv Python 虚拟环境 |

### 代码与符号
| 子维度 | 内容 |
|---|---|
| 核心类 | `ConfigParser`（`prebuilts_service/config_parser.py:23`）、`PoolDownloader`（`prebuilts_service/pool_downloader.py:36`）、`OperateHanlder`（`prebuilts_service/operater.py:39`，注意实际拼写） |
| 核心方法 | `prebuilts_config.py` 主流程（解析→合并同 remote_url→下载→后处理）；`download_util`（sha256 缓存） |
