# Incremental builds after workspace relocation

The supported workflow transfers the complete source workspace together with
its out/ directory, then runs the existing build command:

```bash
./build.sh --product-name wukong100 --build-target make_all --ccache
```

## Automatic baseline and relocation

Before the preloader, loader or GN can rewrite imported build metadata, the
entry script checks out/incremental_workspace.json. A successful explicit
make_all records source input digests, nanosecond timestamps, executable bits,
tool identities, the original logical root and build time. Unchanged builds
refresh that record without collecting all Ninja inputs again. The trailing
`incremental workspace:` messages report this bookkeeping. Single-target
builds retain their requested targets and do not replace the full baseline.

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
python3 -m unittest discover -s build/test/incremental -p 'test_*.py' -v
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
87 .meta files contained host path/time differences. No device was connected,
so device behavior and broader XTS/libc stability suites were not verified.
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
