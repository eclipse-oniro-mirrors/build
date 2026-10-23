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

// Reference implementation of the runtime hook inserted by OurCheckerPass.cpp.
// Build it together with the instrumented code (for the target) so that the
// inserted __raceguard_test_probe calls can be resolved.
//
// The log destination is controlled by RACEGUARD_TEST_PLUGIN_LOG and defaults
// to raceguard_test_plugin.log.

#include <cstdio>
#include <cstdlib>
#include <sys/types.h>
#include <unistd.h>

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

} // namespace

extern "C" void __raceguard_test_probe(const char *event) {
  FILE *stream = getLogStream();
  flockfile(stream);
  std::fprintf(stream, "[raceguard-test-runtime][pid %d] %s\n",
               static_cast<int>(getpid()), event != nullptr ? event : "(null)");
  funlockfile(stream);
}
