# Incremental build regressions

Run from a complete OpenHarmony workspace containing the companion changes:

```bash
python3 -m unittest discover -s build/test/incremental -p 'test_*.py' -v
```

Requires Python 3.10+, the workspace Ninja and Clang prebuilts, bubblewrap,
patch, cpio and gzip. Tests use temporary workspaces and preserve the real out/.
They cover relocation, concurrent independent workspaces, real input changes
before and after relocation, targeted builds, generated-output loss, unchanged
metadata, SDK copy dependencies, patch idempotency, ABI-header propagation,
process validation failures and actual ramdisk bytes. Companion-repository
checks require the matching sdk-js, ets_runtime, ace_engine, sane-airscan and
iptables changes. No device is accessed.

`test_bwrap_bootstrap.py` mocks the package-manager operations. It covers an
already installed binary, root/non-root decisions, update/install failures,
binary rechecks, and same-path versus cross-path dispatch. These unit tests do
not install packages. Actual relocation integration tests still require a
working bubblewrap namespace on the host.
