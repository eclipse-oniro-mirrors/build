#!/usr/bin/env python3
# coding: utf-8
#
# Copyright (c) 2023-2024 Huawei Device Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import subprocess
import argparse
import sys
import shutil
import tempfile


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--src-idl', required=True, help='idl source file')
    parser.add_argument('--dst-path', required=True, help='the converted target path')
    parser.add_argument('--dst-file', required=True, help='the converted target file')
    parser.add_argument('--idl-tool-path', required=True, help='path of the idl conversion tool')
    parser.add_argument('--log-domainid', help='hilog domain id')
    parser.add_argument('--log-tag', help='hilog tag')
    parser.add_argument('--hitrace', help='hitrace switch, default off')
    parser.add_argument('--language', help='language switch, default cpp')
    parser.add_argument('--client-enable', help='gen client code')
    arguments = parser.parse_args()
    return arguments


def run_command(cmd, execution_path):
    print("cmd:", " ".join(cmd))
    print("execution_path:", execution_path)
    # Use subprocess.run and let stdout/stderr inherit so the child never
    # blocks on a full pipe buffer (Popen+PIPE without draining can deadlock
    # when the idl tool emits more than the OS pipe capacity).
    result = subprocess.run(cmd, cwd=execution_path)
    return result.returncode


def _compare_file_contents(fa, fb):
    while True:
        ba = fa.read(65536)
        bb = fb.read(65536)
        if ba != bb:
            return False
        if not ba:
            return True


def files_identical(path_a, path_b):
    """Compare two files byte-by-byte. Returns True if identical."""
    try:
        with open(path_a, 'rb') as fa, open(path_b, 'rb') as fb:
            if os.path.getsize(path_a) != os.path.getsize(path_b):
                return False
            return _compare_file_contents(fa, fb)
    except OSError:
        return False


def ensure_dst_path(dst_path):
    if not os.path.exists(dst_path):
        os.makedirs(dst_path, 0o750, exist_ok=True)


def resolve_rel_dst_files(dst_file_list, dst_path_abs):
    rel_dst_files = []
    for dst_file in dst_file_list:
        rel = os.path.relpath(os.path.abspath(dst_file), dst_path_abs)
        rel_dst_files.append(rel)
    return rel_dst_files


def resolve_gen_language(language):
    if language == "rust":
        return "-gen-rust"
    if language == "ts":
        return "-gen-ts"
    return "-gen-cpp"


def build_idl_command(input_arguments, tmp_dst_dir, src_idl, gen_language):
    cmd = [os.path.join(".", "idl"), gen_language, "-d", tmp_dst_dir, "-c", src_idl]
    if input_arguments.log_domainid:
        cmd += ['-log-domainid', input_arguments.log_domainid]
    if input_arguments.log_tag:
        cmd += ['-log-tag', input_arguments.log_tag]
    if input_arguments.hitrace:
        cmd += ['-t', input_arguments.hitrace]
    if input_arguments.client_enable == 'true':
        cmd += ['-client-enable']
    return cmd


def run_idl_generation(input_arguments, tmp_dst_dir, src_idl_list, gen_language):
    for src_idl in src_idl_list:
        cmd = build_idl_command(input_arguments, tmp_dst_dir, src_idl, gen_language)
        ret = run_command(cmd, input_arguments.idl_tool_path)
        if ret != 0:
            return ret
    return 0


def locate_tmp_file(tmp_dst_dir, rel_file, dst_file):
    tmp_file = os.path.join(tmp_dst_dir, rel_file)
    if os.path.exists(tmp_file):
        return tmp_file
    tmp_file = os.path.join(tmp_dst_dir, os.path.basename(dst_file))
    if os.path.exists(tmp_file):
        return tmp_file
    return None


def move_file_if_changed(tmp_file, dst_file):
    if os.path.exists(dst_file) and files_identical(tmp_file, dst_file):
        os.remove(tmp_file)
        return False
    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
    shutil.move(tmp_file, dst_file)
    return True


def move_declared_outputs(tmp_dst_dir, dst_file_list, rel_dst_files):
    # Move each newly generated file into place only when its content
    # differs from the existing output. Unchanged files keep their original
    # mtime, avoiding spurious downstream rebuilds.
    moved_files = set()
    for dst_file, rel_file in zip(dst_file_list, rel_dst_files):
        tmp_file = locate_tmp_file(tmp_dst_dir, rel_file, dst_file)
        if tmp_file is None:
            # A declared output was not produced by the idl tool. Warn loudly:
            # a missing output keeps ninja re-running this action every build
            # (incremental build never settles) and usually signals a mismatch
            # between the GN template's outputs list and what the tool actually
            # emits (e.g. an idl target that enables client_enable but the tool
            # does not generate *_client.cpp for that idl).
            print(
                "WARNING: idl_gen_interface declared output was not "
                "generated by the idl tool: {}".format(dst_file),
                file=sys.stderr,
            )
            continue
        if move_file_if_changed(tmp_file, dst_file):
            moved_files.add(dst_file)
        else:
            moved_files.add(tmp_file)
    return moved_files


def move_remaining_outputs(tmp_dst_dir, dst_path):
    # The idl tool may generate files whose names differ from the GN
    # template's declared outputs (e.g. the interface header "iv_a_stream.h"
    # generated by the tool vs "iva_stream.h" declared by the template).
    # Move any remaining files from tmp_dir to dst_path so that downstream
    # #include directives resolve correctly, especially on clean builds
    # where no stale copy exists in dst_path.
    for root, dirs, files in os.walk(tmp_dst_dir):
        for fname in files:
            tmp_file = os.path.join(root, fname)
            rel = os.path.relpath(tmp_file, tmp_dst_dir)
            dst_file = os.path.join(dst_path, rel)
            if os.path.exists(dst_file) and files_identical(tmp_file, dst_file):
                os.remove(tmp_file)
                continue
            os.makedirs(os.path.dirname(dst_file), exist_ok=True)
            shutil.move(tmp_file, dst_file)


def idl_gen_interface(input_arguments):
    ensure_dst_path(input_arguments.dst_path)

    dst_file_list = input_arguments.dst_file.split(',')

    # Resolve each declared output to a path relative to dst_path, so that we
    # can locate the freshly generated copy inside the temporary directory.
    dst_path_abs = os.path.abspath(input_arguments.dst_path)
    rel_dst_files = resolve_rel_dst_files(dst_file_list, dst_path_abs)

    gen_language = resolve_gen_language(input_arguments.language)

    src_idl_list = input_arguments.src_idl.split(",")

    # Generate into a temporary directory first, then only replace outputs that
    # actually changed. This preserves mtime of unchanged files so that ninja's
    # incremental build does not cascade-rebuild downstream .o targets.
    gen_marker = os.sep + "gen" + os.sep
    gen_idx = dst_path_abs.find(gen_marker)
    tmp_base = os.path.dirname(dst_path_abs[:gen_idx]) if gen_idx != -1 else os.path.dirname(dst_path_abs)
    tmp_dst_dir = tempfile.mkdtemp(prefix="idl_gen_", dir=tmp_base)
    try:
        all_ret = run_idl_generation(input_arguments, tmp_dst_dir, src_idl_list, gen_language)
        if all_ret != 0:
            return all_ret

        move_declared_outputs(tmp_dst_dir, dst_file_list, rel_dst_files)
        move_remaining_outputs(tmp_dst_dir, input_arguments.dst_path)

        return 0
    finally:
        shutil.rmtree(tmp_dst_dir, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(idl_gen_interface(parse_args()))
