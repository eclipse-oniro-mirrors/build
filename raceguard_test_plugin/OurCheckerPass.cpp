#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <sys/types.h>
#include <unistd.h>

#include "llvm/IR/Function.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/PassManager.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"

using namespace llvm;

namespace {

FILE *getLogStream() {
  static FILE *logStream = nullptr;
  FILE *stream = __atomic_load_n(&logStream, __ATOMIC_ACQUIRE);
  if (stream != nullptr)
    return stream;
  const char *path = std::getenv("RACEGUARD_TEST_PLUGIN_LOG");
  if (path == nullptr || path[0] == '\0')
    path = "raceguard_test_plugin.log";
  FILE *opened = std::fopen(path, "a");
  if (opened == nullptr)
    opened = stderr;
  FILE *expected = nullptr;
  if (__atomic_compare_exchange_n(&logStream, &expected, opened, false,
                                  __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
    return opened;
  if (opened != stderr)
    std::fclose(opened);
  return expected;
}

void logLine(const char *format, ...) {
  FILE *stream = getLogStream();
  std::fprintf(stream, "[raceguard-test-plugin][pid %d] ",
               static_cast<int>(getpid()));
  va_list args;
  va_start(args, format);
  std::vfprintf(stream, format, args);
  va_end(args);
}

}

void *operator new(size_t size) {
  void *mem = std::malloc(size == 0 ? 1 : size);
  if (mem == nullptr) {
    logLine("operator new: out of memory\n");
    std::abort();
  }
  return mem;
}

void operator delete(void *mem) noexcept { std::free(mem); }

void operator delete(void *mem, size_t) noexcept { std::free(mem); }

namespace {

class RaceguardTestPass : public PassInfoMixin<RaceguardTestPass> {
public:
  PreservedAnalyses run(Module &M, ModuleAnalysisManager &) {
    unsigned defined = 0;
    for (const Function &F : M) {
      if (!F.isDeclaration())
        ++defined;
    }
    logLine("module '%.*s' defined functions: %u\n",
            static_cast<int>(M.getName().size()), M.getName().data(), defined);
    for (const Function &F : M) {
      if (!F.isDeclaration())
        logLine("  function '%.*s'\n",
                static_cast<int>(F.getName().size()), F.getName().data());
    }
    return PreservedAnalyses::all();
  }
};

void registerCallbacks(PassBuilder &PB) {
  logLine("plugin loaded, registering callbacks\n");
  PB.registerPipelineStartEPCallback(
      [](ModulePassManager &MPM, OptimizationLevel) {
        logLine("pipeline start EP fired\n");
        MPM.addPass(RaceguardTestPass());
      });
}

}

extern "C" LLVM_ATTRIBUTE_WEAK PassPluginLibraryInfo llvmGetPassPluginInfo() {
  return {LLVM_PLUGIN_API_VERSION, "raceguard-test-plugin", "0.1.1",
          registerCallbacks};
}
