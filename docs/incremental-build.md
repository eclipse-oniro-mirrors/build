# Incremental builds after workspace relocation

The supported workflow transfers the complete source workspace together with
its out/ directory, then runs the existing build command:

```bash
./build.sh --product-name x86_64_virt --build-target make_all --ccache \
  --gn-args runtime_core_enable_codegen=false --deps-guard=false
```

## Automatic baseline and relocation

Before the preloader, loader or GN can rewrite imported build metadata, the
entry script first reads the product feature. Only enabled products check their
actual output folder/incremental_workspace.json (see Product opt-in below). A successful explicit
make_all records source input digests, nanosecond timestamps, executable bits,
tool identities, the original logical root and build time. Unchanged builds
refresh that record without collecting all Ninja inputs again. The trailing
`incremental workspace:` messages report this bookkeeping. Single-target
builds retain their requested targets and do not replace the full baseline.

Successful full-product seals also atomically acknowledge the exact manifest
in `<actual product output>/incremental_workspace.restore-state.json`. The restore key includes the
manifest content hash, host boot identity, root device/inode and physical path.
Normal successful refreshes update this key, so the next build in the same
physical workspace does not hash all transferred inputs again. A rename, copy,
host change or imported manifest still triggers a conservative first check,
including source changes that preserved their old modification times. Existing
state without the physical-path field is checked once after upgrading.

The normal mapped build entry validates each distinct tool file once; aliases
share a digest only within that validation call. Direct restore still validates
tools. Failed/interrupted builds do not acknowledge a new successful baseline.
A restore-cache write failure warns and retains the conservative recheck path.

Entry logs distinguish mountpoint preparation, mapped-root readiness, tool
validation and input restore/skip. Full capture logs report its decision reason,
Ninja audit/inputs/deps, input and tool fingerprinting, manifest write and total
time. Long input scans report progress. These are bookkeeping phases; collecting
Ninja inputs/deps does not execute compilation. Unchanged successful builds keep
the existing fast refresh, with no full input/dependency recollection. Actual
configuration, dependency or source-state changes still require a new capture.

The incremental helpers write their depfile input lists relative to Ninja's
working directory, not to the directory containing the .d file. Output target
spelling remains unchanged to match the build graph. The shared writer keeps
its original default; only these helpers opt in. SDK copy, standard-library
and SELinux helpers perform the same conversion locally and retain their
existing build-utility API. Paths on different Windows drives keep their
original spelling when no relative path exists.

Full capture also records reachable retained action depfiles, including their
transitive input closure. Ninja's inputs tool is asked for unescaped paths so
spaces are not mistaken for shell quoting. Old inventories upgrade once at a
successful full capture. Stable finishes check known depfile metadata; a
timestamp-only rewrite with identical bytes refreshes that metadata without
recollecting all inputs. A changed dependency list requires a new capture.
This inventory does not rewrite .ninja_deps, and compiler-produced depfiles
remain controlled by their original compiler rules. Relative depfiles do not
remove the need to map existing absolute build commands and CMake paths.

On a transferred workspace, identical inputs regain their recorded timestamps.
Real edits, additions and deletions remain visible to Ninja, including edits
made before the first local build. A private bubblewrap mount namespace presents
the imported workspace at its original logical root. The host does not acquire
a placeholder directory. Independent copies use separate namespaces, baselines
and ccache log suffixes. A retained, nonempty original root is shadowed only
inside the consumer's mount namespace; its host files remain unchanged.
If the original root itself is a symlink to another job, its parent is recreated
privately and that link is replaced by a directory only in the child namespace.
The host symlink and its target are not changed or used as consumer output.
Preparing that replacement does not stat the old link target. Legacy CMake
discovery checks entry names before probing matching clang_* cache paths, so
an unrelated out/<product>/src link cannot abort discovery. I/O errors from
the actual source/output filesystem or a relevant CMake cache still fail
explicitly; these checks do not turn filesystem failures into successful builds.

Install bubblewrap on Linux before relocating a workspace. Ordinary in-place
builds do not require a namespace and retain their build-time behavior. Keep
symlinks, nanosecond mtimes, .ninja_log and .ninja_deps when archiving the complete
workspace; the CI build must include this entry-script change. Legacy output
without a baseline first checks flatbuffers CMakeCache.txt source and binary
roots for agreement, then falls back to out/ohos_config.json. This recovers old
CI roots even when HB has already rewritten or removed ohos_config.json. It
cannot restore source timestamps until a successful build records them. An
old snapshot first patched after export may therefore do substantial real work.
The cache check also runs before trusting an existing manifest's in-place
shortcut. A consistent flatbuffers cache with a different root replaces that
manifest with an unsealed path-only record; its unverified fingerprints are
discarded. A remaining CMake cache can recover the root even if build.ninja was
removed before GN regeneration. ohos_config.json alone still requires an
existing product graph and never overrides an existing manifest. Unknown
historical fingerprints do not provide a zero-work guarantee.
Deleting out keeps the ordinary clean-build path and creates no namespace.
Prebuilt inputs outside out must still be prepared with the project downloader;
for example JSVM requires its pinned V8 library and headers under vendor/.
Toolchain changes are rejected instead of silently reusing incompatible output.
Only the explicitly requested make_all captures a complete successful baseline.

The mechanism does not rewrite compiler commands, Ninja command hashes,
binary dependency databases, public GN defaults or __FILE__ expansion. It
does not modify or flash device software. External build environments and
shared services still need to be managed separately. Concurrent builds of
the very same physical out/ directory are not supported.

## Reusing baseline collection indexes

Successful enabled `make_all` builds retain an internal index at
`<actual product output>/.incremental/collection.json`. The manifest binds its
schema, generation and content digest to the selected product. Missing, corrupt
or incompatible indexes fall back to a complete capture; downloaded tools and
source-tree intermediates are never removed to construct this index.

The index covers the complete literal Ninja include/subninja graph, dependency
database, retained depfile inventory, transitive input closure and source
fingerprints. Unsupported include syntax falls back to Ninja tool queries.
Directory identities discover added/deleted depfiles. Source reuse checks device,
inode, size, modification time, change time and mode on the same filesystem
instance; timestamps alone do not prove unchanged contents. Parsed depfile rules
are bounded to 64 MiB, with deferred parsing for reachable uncached rules.

An unchanged finish validates that inventory without rebuilding the parsed
closure. It also checks previously absent input candidates and tool identities,
and advances the output timestamp ceiling when unchanged sources rebuild a
missing output. Changed graphs, dependencies or inputs use the indexed collector.
Across copies or hosts, entry-verified fingerprints are valid only for the same
build invocation; subsequent changes still require hashing. Journals are removed
after finishing, and each product uses its actual output directory.

This bookkeeping does not rewrite compiler/linker commands, skip Ninja, fabricate
outputs or change device payloads. Disabled products do not enter this path.
Stable existing inner-kit/system-kit/test-target ordering is preserved while
retaining the current target membership.

## Correct incremental dependencies

Content-aware writes preserve unchanged JSON, formatted GN metadata and existing
component ordering. IDL output names follow the emitter and shared imports have
a single producer. Optional metadata and process validation track meaningful
inputs without depending on their own temporary outputs. Permission and signing
failures still fail the build. SDK, empty JS archives and signing actions emit
accurate completion markers and depfiles; missing real generated files invalidate
the action. Shader compilation has a success-only wrapper and tracks sources
and generated files. Ramdisk declarations receive copies of the actual original
cpio/gzip image bytes rather than placeholder files. Static standard-library
config files are isolated by build target without altering their compiler options.

Companion fixes cover SDK declaration copies, frontend consistency checks,
standard-library depfiles, Arkoala native-header content signatures, HDC and
updater hash headers, SELinux completion markers, JSVM depfile spelling,
css-what/jsframework producer ownership, idempotent sane-airscan patching and
stable iptables registry generation. Apply the companion repository PRs as a
coordinated set; graphic_3d requires scripts/run_shader_compile.py from this PR,
and the runtime standard-library depfile changes accompany the build wrapper.

## Validation

The full wukong100 workspace was physically renamed and built using the original
command, then renamed back and built again. On the clean baseline, both runs
executed one existing optional eng_chipset image probe and zero C/C++ compile
steps. Complete wall times including baseline handling were 103.12 seconds
after relocation and 86.10 seconds after restoring the directory. A real HDC
single-target build also passed with zero executed steps. These measurements
are specific to the tested workspace and host.

```bash
python3 /path/to/incremental/openharmony/tests/build/run_tests.py --source-root /path/to/oh
```

All 42 focused regressions passed, including eight legacy/cold-entry cases.
A real CMake/Ninja fixture is built before installing the new entry, moved,
and rebuilt through the production dispatch hook with missing or rewritten
ohos_config.json. Both variants compile
and execute successfully without host placeholder directories. The tests perform real Ninja/Clang builds
and execute changed consumers, check missing outputs and failing permission
validation, compare actual ramdisk bytes and exercise concurrent copies. The
original and isolated-config standard-library ABC files were byte-identical.
Shader payload comparison covered 544 packed shader files with equal bytes;
87 .meta files contained host path/time differences. These checks cover the
reported host build and artifact comparisons.
The build/test/script/start_ex.sh suites remove out/ before running and were
not executed against this retained workspace.

## Missing bubblewrap at the build entry

The build entry reads the existing workspace manifest before preloader, GN or
Ninja runs. When the physical workspace differs from the recorded logical root,
the helper checks for `bwrap` before constructing the private mount namespace.
An available binary is used directly, without checking privileges or invoking
the package manager. Ordinary same-path builds do not perform this check.

If `bwrap` is missing, root runs `apt update` followed by
`apt install -y bubblewrap`; the install is not attempted if the update fails.
The helper checks that the binary is available afterwards. A non-root user
gets a failure before build preparation, with this recovery command:

```bash
sudo apt update && sudo apt install -y bubblewrap
```

After installation, rerun the original build command. Package-manager failures
or a still-missing binary terminate the build with the recovery instructions.
The package bootstrap does not fall back to compiling against unmapped paths.

## Existing independent proposals

This series does not include the existing proposals ICU !481, graphic_3d !686,
libtiff !47, ets_frontend !11396 or build !7182. In particular it does not enable
global -ffile-prefix-map: that option alone does not relocate CMake caches,
Ninja dependency databases or absolute tool paths. Those PRs remain separate.


## Product opt-in for workspace reuse

`build_framework_enable_incremental_build` is registered in `build/bundle.json`
and declared in `build/ohos_var.gni` as a boolean defaulting to `false`. Configure
it in the selected product's `build_framework` component features:

```json
{
  "subsystem": "build",
  "components": [{
    "component": "build_framework",
    "features": ["build_framework_enable_incremental_build = true"]
  }]
}
```

Only the x86_64_virt product configuration opts in. This is configuration, not a
product-name or CPU check in the build framework. Product inheritance and part
overrides follow HB's shared read-only product enumeration. A conflicting
`--gn-args` override is rejected; users keep the normal build command.

The feature controls baseline capture/validation, transferred input timestamp
restoration, path relocation and the build entry's bubblewrap bootstrap. Correct
Ninja input/output declarations, HAP/SDK recovery and other repository fixes
remain active independently. OSKM's separate LSP namespace is unaffected.

Disabled builds do not enter the workspace helper or read its manifests, CMake
root evidence or restore state. Snapshot environment variables are ignored.
Enabled fresh calls clear stale snapshot values; internal re-entry is bound to
product/configuration, the physical directory identity and the actual shell PID.
SDK subprocesses do not own or overwrite the top-level product's baseline.

New baseline and restore state files are stored in the actual product output
folder (`out/<board>`, host/small-system/custom paths follow HB). They bind the
selected product, company, configuration path and workspace policy version. An
old global v1 baseline is imported only when the enabled selected product, build
folder and recorded GN argument identity match. Disabled products never import
or delete it. An enabled external output must be inside the transferred tree.

After changing the feature or selected product, first run without
`--fast-rebuild`. Later normal and fast source increments keep their usual
semantics; a single target never promotes itself to make_all or captures a new
complete baseline. Explicit `--product-name` is required for automatic workspace
reuse; menu/direct HB calls keep their normal build behavior.

Regression tests are maintained in the personal repository, outside this PR:
https://gitcode.com/ringking0/incremental/tree/main/openharmony/tests/build
They run only when explicitly invoked by a user or gate, never during product
compilation. The original test snapshot is retained there before removal from
the PR's final diff.
### 收尾阶段的有界并行

完整基线收集时，Ninja 图检查、保留 depfile 清单扫描，以及需要重新查询的
Ninja inputs/deps 可以在有界线程池内重叠执行。所有任务完成后，才展开依赖闭包、
计算输入指纹和提交基线；编译命令返回时不会遗留后台基线任务。

`OHOS_INCREMENTAL_JOBS` 控制指纹并行预算，支持 1～32，默认取 CPU 亲和性允许数
与 8 的较小值；独立收集阶段最多同时执行 `min(3, OHOS_INCREMENTAL_JOBS)` 个任务。
设置为 1 可串行对照。
两个 Ninja 查询使用 `-n`，正常有效日志下不会触发构建写入或重整日志。
Ninja 对损坏或版本不支持的 deps 日志仍可能执行自身的恢复处理。
该设置不进入 GN 参数，
不改变产品开关、单目标边界或构建产物。复用指纹时通常没有足够的哈希任务，
因此线程数更多不代表收尾一定更快，应按实际文件系统和资源限制对照测量。

需要重新计算的指纹按每片 128 个文件分配，单窗口最多提交工作单元数两倍的分片，
工作单元只返回本片结果，由主进程按输入顺序汇总。Linux 上至少 4096 个文件需重算、
预算大于 1 且调用进程没有其它活动线程时，使用最多 8 个独立启动的工作进程，
分片间不共享结果写入；
少量修改及其它环境使用分片线程池。大部分指纹可复用的增量不会为了凑并发创建
多个工作进程。所有工作进程和线程均在返回前回收，读取失败不提交半份基线。

普通增量的路径整理和 stat 检查也可分片：本地缓存有效、至少 4096 条路径、
预算大于 1 且主进程没有其它活动线程时，每片 4096 条路径，最多 4 个 spawn
进程独立检查，窗口最多两倍工作进程数。各片使用自己的路径缓存，主进程按原始
排序顺序合并，保留软链接别名去重和完整 stat 比较；不共享可写结果字典。
无本地缓存的全量收集保持原来的串行路径整理，再进入已有的指纹分片，避免额外
创建一组路径进程。少量路径、单工作单元和其它平台保持串行路径整理。

索引与基线使用紧凑 JSON 编码，字段、值、版本、摘要校验和读取方式不变。
索引仍先准备并绑定摘要，基线由主进程最后原子发布；不会并行覆盖同一个文件。
依赖查询继续调用原有官方 Ninja 命令，不解析或改写二进制 deps 日志来省略查询。
