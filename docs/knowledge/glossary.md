# 术语表

> 路径约定：源码路径均相对 build 仓根目录。数值与默认值均为当前 master 实测，未经核实的不写。

## 子系统（subsystem / `subsystem_config.json`）
OpenHarmony 源码的组织单位，一个子系统对应一组源码路径。`build/subsystem_config.json` 是全局子系统名→路径注册表（如 `"arkui": {"path": "foundation/arkui", "name": "arkui"}`），产品可用 `subsystem_config_overlay.json` 追加。loader（`hb/util/loader/subsystem_scan.py`）据此决定扫描哪些目录。**子系统名不是可构建目标**，不能传给 `--build-target`。与"部件"是包含关系：一个子系统含多个部件。

## 部件（component / part / `bundle.json`）
可独立配置、可复用的功能单元，部件模型的最小裁剪单位。每个部件由其源码仓根 `bundle.json` 的 `component` 节定义，关键子节：`sub_component`（构建入口 GN 目标）、`inner_kits`（跨部件接口）、`test`（测试目标）、`conditions`（条件编译）。loader 中对应实体为 `BundlePartObj`（`hb/util/loader/load_bundle_file.py`，`to_ohos_build()` 归一）与 `PartObject`（`hb/util/loader/load_ohos_build.py`），最终生成 `out/{board}/build_configs/{subsystem}/{part}/BUILD.gn`。`--build-target` 完整形式即 `out/{device}/build_configs/{subsystem}/{part}:{part}`。

## 模块（module / `ohos_*` 模板目标）
部件内的一个构建单元，即业务仓 BUILD.gn 中用 `ohos_shared_library`、`ohos_executable`、`ohos_prebuilt_etc` 等模板定义的目标。每个模块通过 `generate_module_info`（`templates/metadata/module_info.gni`）产出 `{module}_module_info.json`（含 source/dest/install_images 等），是安装与打包的数据源。

## 部件模型四配置文件
本仓 README_zh.md 归纳的四层配置链：模块 `BUILD.gn` → 部件 `bundle.json` → 产品 `vendor/{company}/{product}/config.json` → 全局 `build/subsystem_config.json`。模块进镜像三条件：有 `part_name`、在部件 `sub_component`（或被其依赖）、部件在产品部件列表中。

## external_deps（跨部件依赖）
格式 `"部件名:模块名"` 的模块间依赖表达，被依赖模块必须声明在对方 `inner_kits`。解析脚本 `templates/common/external_deps_handler.py`：读 `build_configs/parts_info/inner_kits_info.json`，按 variant 拼 `label(toolchain)` 加入 deps，so 类补头文件路径；支持 `component_override_map` 与 `inner_kits_adapter.json` 兼容映射。与部件内 `deps`（裸 GN label）相对。

## inner_kits（部件间接口）
bundle.json `component.build.inner_kits` 节，声明本部件提供给其他部件的接口模块（头文件+库）。是 external_deps 的被依赖侧，也是 `make_inner_kits` 顶层目标的来源。

## preloader（预加载器）
hb 构建第一阶段（`hb/services/preloader.py` + `hb/util/preloader/`）：解析产品 config.json（v2/v3 parts 继承链），产出 `out/preloader/{product}/` 下的 build.prop、parts.json、features.json、syscap.json、SystemCapability.json、build_gnargs.prop（部件 features→gn args）等配置集，供 loader 与 GN 消费。mini/small 系统改走 `parse_lite_subsystems_config.py` 解析 `build/lite/components`。

## loader（加载器）
hb 构建第二阶段（`hb/services/loader.py` + `hb/util/loader/`）：按子系统注册表多进程扫描源码树 bundle.json/ohos.build，完成部件覆盖（`component.override`）、平台变体、条件编译匹配、裁剪校验后，生成 `out/{board}/build_configs/` 下的部件 BUILD.gn、parts_info/*.json、subsystem_info/、phony_target 等。`--fast-rebuild` 会跳过本阶段。

## hb（ohos-build）
本仓提供的命令行工具（`hb/`，pip 包名 `ohos-build`），子命令 build/init/indep_build/set/env/clean/tool/install/package/publish/update/push（`hb/main.py` 注册）。`build.sh` 默认分发到 `hb/main.py build`；`--using_hb_new=false` 走旧路径 `scripts/entry.py`。

## dotfile.gn / BUILDCONFIG.gn
GN 的全局配置链：源码根 `.gn` 软链到 `core/gn/dotfile.gn`，其 `buildconfig` 指向 `config/BUILDCONFIG.gn`（产品级参数与编译配置入口），`root` 指向 `build/core/gn`；`exec_script_whitelist` 限制 exec_script 白名单，`ohos_components_support = true` 启用定制 GN 的 external_deps 支持。独立编译时 `.gn` 改链 `build/indep_configs/dotfile.gn`。

## declare_args（GN 构建参数）
GN 参数声明机制。本仓分层：`common.gni`（build_variant 等 4 个）、`version.gni`（版本组）、`ohos_var.gni`（40+ 组主参数：目录基址、SDK 开关、行为开关、工具链）、`config/BUILDCONFIG.gn`（产品级：product_name、is_standard_system、support_jsapi 等）。命令行 `--gn-args key=value` 覆盖默认值。

## install_images（安装分区）
模块属性（默认 `["system"]`），取值 system/ramdisk/vendor/updater/updater_vendor/sys_prod/chip_prod/eng_system/eng_chipset/cloud_rom/system_arm64e；`gen_module_info.py` 按分区映射到 `ohos_var.gni` 的 base_dir 拼 dest。安装路径优先级：`module_install_dir` > `relative_install_dir`（相对 `base/{module_type}`）> 默认 `base/{module_type}`。

## install_enable
模块是否安装进镜像的开关。当前 master：`ohos_shared_library`/`ohos_executable` 默认 true（executable 仅 `is_double_framework` 时 false），`ohos_copy`/`ohos_prebuilt_executable` 默认 false；README_zh.md「默认不安装」为历史表述。`modules_install.py` 对 `install_enable=false` 的模块直接跳过。

## syscap / SystemCapability
系统能力（SystemCapability）集合，设备裁剪的粒度。bundle.json `component.syscap` 声明，preloader 生成 `out/preloader/{product}/system/etc/{SystemCapability.json,syscap.json,param/syscap.para}`，loader 校验命名必须 `SystemCapability.` 前缀（错误码 2008/2009）。NDK 侧另有 `ndk_system_capability.json`。

## 独立编译（indep / independent build）
`hb build {component} -i`：只需 build 仓 + 目标部件仓，其余依赖按 bundle.json `component.deps` 从 repo.harmonyos.com 下载二进制包到 `~/.hpm`（或 `--local-binarys` 本地目录），external_deps 解析为二进制依赖。自动触发条件受 `indep_component_whitelist.json` 约束（当前仅 rk3568 等产品），`--no-indep` 逃逸；执行目录须含 `build/indep_configs/build_indep.sh`；`.gn` 临时切换到 `build/indep_configs/dotfile.gn`（trap 恢复）。

## prebuilts（预编译工具链）
构建所需的宿主工具集合（node/cmake/gn/ninja/clang/python/rust/hvigor/cangjie/hpm 等 29 类，`prebuilts_config.json` 声明，按平台×CPU 匹配 remote_url+sha256）。全量构建下载到 `../openharmony_prebuilts`（源码树旁），独立编译下载到 `~/.prebuilts_cache`；下载引擎 `prebuilts_service/`（ConfigParser + PoolDownloader 线程池 + OperateHanlder 后处理）。

## products_ext（产品级覆盖）
`out/products_ext/{product}/` 目录机制：白名单与部分配置优先读该目录下同名文件（如 compile_standard_whitelist.json、auto_install_whitelist.json、vendor 扩展产品），不存在再回落 `build/` 默认文件。产品差异不改公共配置面的标准途径。

## .pydeps
Python 脚本依赖清单（`print_python_deps.py` 枚举非系统 python 依赖生成），**生成文件，勿手改**——GN action 借它声明脚本输入以支持正确增量。

## 顶层构建目标（make_all / images / packages / make_test）
`core/gn/BUILD.gn` 定义：默认 ninja 目标为 `images`（→ `//build/ohos/images:make_images`）；`make_all` = make_inner_kits + packages + images（标准系统且非 llvm）；`packages` → `//build/ohos/packages:make_packages`；`-f`/`--full-compilation` 追加 `make_all + make_test`。

## os_level（mini / small / standard）
产品配置 config.json 的系统级别字段。standard 走完整 GN+mkimage 链路（out 路径 `out/{board}`）；mini/small 走 lite 方案（`build/lite/`，out 路径 `out/{board}/{product}`，preloader 解析 `build/lite/components`，打包用 `fs_process.py` 的 packer 而非 mkimage）。

## mkimage（镜像制作）
`ohos/images/mkimage/` 工具族：`mkimages.py` 按文件系统类型分发 `mkextimage.py`（mke2fs+e2fsdroid，吃 dac.txt 与 SELinux file_contexts）/`mkf2fsimage.py`（mkfs.f2fs+sload.f2fs）/`mkcpioimage.py`（cpio）。镜像参数（挂载点/大小/flags）来自 `mkimage/*_image_conf.txt`，设备可用 `packages/{product}/imagesconf/` 覆盖。实测默认：system=ext4 2GiB、vendor=ext4 256MiB、userdata=f2fs 1.4GiB。

## Notice / OAT / SBOM
开源合规三件套：**Notice** 是许可声明文本（模块级三级回退收集 → `merge_notice_files.py` 按内容 hash 去重合并为 NOTICE.txt/NOTICE.xml.gz）；**OAT**（`OAT.xml`）是 OpenHarmony OSS Audit Tool 的静态审计工程配置，不在 ninja 流水线内；**SBOM**（`--sbom` 触发 `ohos/sbom/generate_sbom.py`）产出 SPDX 2.3 清单（`sbom_meta_data.json` + `spdx.json`），依赖 gn_gen.json 与 repo manifest。

## sanitize（asan / tsan / ubsan / scudo / scs）
内存与线程安全编译插桩体系（`config/sanitizers/sanitizers.gni` 的 `ohos_sanitizer_config`），模块通过 `sanitize` 属性开启；`allow_sanitize_debug`（`build_xts=true` 时）跳过检查；asan/tsan 非 standard 时安装到 `system/asan`。

## innerapi_tags（ndk / llndk / chipsetsdk / platformsdk / passthrough / sasdk）
共享库的接口分发标签，自动决定安装目录并做互斥断言：ndk → `ndk` 目录、chipsetsdk → `chipset-sdk`、platformsdk → `platformsdk`、passthrough → `chipsetsdk` 等（目录基址见 `ohos_var.gni`）。用于把对外/对内/芯片专属 so 分发到不同 SDK 面。

## ohos-sdk（产品）
特殊产品名：`./build.sh --product-name ohos-sdk` 构建 SDK（`build_ohos_sdk/build_ohos_ndk=true`、target_cpu=arm64），产物 `out/sdk/packages/ohos-sdk/`，由描述文件 `ohos/sdk/ohos_sdk_description_std.json`（205 项 install_dir+module_label+target_os）驱动多平台（linux/windows/darwin）打包。NDK 目标清单来自 `//interface/sdk_c/ndk_targets.gni`。

## 构建变体（build_variant）
`common.gni` 声明（默认 `"root"`，可选 user 等），影响镜像内容：`build_variant=user` 时 eng_system 镜像产出空目录、root_image.json 拷贝不生效（`ohos/images/build_image.py`）。

## build_tracker / DFX
`dfx/` 提供的构建埋点机制：`build_tracker` 装饰器包裹 hb 各阶段方法，按 `dfx_config.json`（build_dfx_enable、上报 API、加密 header）输出 trace 到 `out/dfx/`。与业务日志（build.log）互补，用于构建行为分析。
