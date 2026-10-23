/*
 * Copyright (c) 2024 Huawei Device Co., Ltd.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <sys/types.h>
#include <unistd.h>

#include "llvm/ADT/StringRef.h"
#include "llvm/IR/Attributes.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/PassManager.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"

using namespace llvm;

namespace {

constexpr const char *kProbeName = "__raceguard_test_probe";

// Compile-time log, written by the compiler process that loaded the plugin.
// The destination is controlled by RACEGUARD_TEST_PLUGIN_LOG and defaults to
// raceguard_test_plugin.log.
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
  flockfile(stream);
  std::fprintf(stream, "[raceguard-test-plugin][pid %d] ",
               static_cast<int>(getpid()));
  va_list args;
  va_start(args, format);
  std::vfprintf(stream, format, args);
  va_end(args);
  funlockfile(stream);
}

} // namespace

// The plugin is loaded into clang with -nostdlib++, so keep the global
// allocation operators local. They must be defined at global scope.
void *operator new(size_t size) {
  void *mem = std::malloc(size == 0 ? 1 : size);
  if (mem == nullptr) {
    std::fprintf(stderr, "raceguard-test-plugin: out of memory\n");
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
    LLVMContext &ctx = M.getContext();
    FunctionType *probeTy = FunctionType::get(
        Type::getVoidTy(ctx), {PointerType::getUnqual(ctx)}, false);
    FunctionCallee probe = M.getOrInsertFunction(kProbeName, probeTy);

    unsigned definedFunctions = 0;
    unsigned insertedProbes = 0;
    for (Function &F : M) {
      if (F.isDeclaration())
        continue;
      ++definedFunctions;
      if (F.getName() == kProbeName || F.isIntrinsic() ||
          F.hasAvailableExternallyLinkage() ||
          F.hasFnAttribute(Attribute::Naked))
        continue;

      BasicBlock &entry = F.getEntryBlock();
      if (entry.empty())
        continue;

      StringRef name = F.getName();
      char message[256];
      int length = std::snprintf(message, sizeof(message), "enter:%.*s",
                                 static_cast<int>(name.size()), name.data());
      if (length < 0)
        continue;
      if (static_cast<size_t>(length) >= sizeof(message))
        length = static_cast<int>(sizeof(message)) - 1;

      IRBuilder<> builder(&*entry.getFirstInsertionPt());
      Value *event = builder.CreateGlobalStringPtr(
          StringRef(message, static_cast<size_t>(length)));
      builder.CreateCall(probe, {event});
      ++insertedProbes;
      logLine("inserted %s into function '%.*s'\n", kProbeName,
              static_cast<int>(name.size()), name.data());
    }

    logLine("module '%.*s': defined functions=%u, probes inserted=%u\n",
            static_cast<int>(M.getName().size()), M.getName().data(),
            definedFunctions, insertedProbes);
    return PreservedAnalyses::none();
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

} // namespace

extern "C" LLVM_ATTRIBUTE_WEAK PassPluginLibraryInfo llvmGetPassPluginInfo() {
  return {LLVM_PLUGIN_API_VERSION, "raceguard-test-plugin", "0.2.0",
          registerCallbacks};
}
