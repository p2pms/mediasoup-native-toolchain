#!/usr/bin/env python3
"""
Build WebRTC static library from source using depot_tools and GN/Ninja.

Produces:
  output/webrtc/
    ├── include/          # WebRTC headers (filtered)
    └── lib/
        └── webrtc.lib

Or (with --package):
  output/archive/webrtc-{branch}-win-{arch}-{config}.zip
"""

import os
import sys
import argparse
import shutil
import subprocess

from utils import (
    run_cmd,
    ensure_dir,
    clean_dir,
    ensure_output_layout,
    get_output_layout,
)
from load_config import get as config


####################################################################################################
# Parse command-line arguments
####################################################################################################
def parse_args():
    parser = argparse.ArgumentParser(description="Build WebRTC static library")
    parser.add_argument("--branch", type=str, default=config("webrtc.default_branch"),
                        help=f"WebRTC branch to build")
    parser.add_argument("--arch", type=str, default=config("toolchain.default_arch"),
                        choices=["x64", "x86", "arm64"],
                        help=f"Target architecture")
    parser.add_argument("--config", type=str, default=config("toolchain.default_config"),
                        choices=["Release", "Debug"],
                        help=f"Build configuration")
    parser.add_argument("--build", action="store_true",
                        help="Build after generating GN files")
    parser.add_argument("--package", action="store_true",
                        help="Build and package into a zip archive")
    parser.add_argument("--clean", action="store_true",
                        help="Clean output before building")
    return parser.parse_args()


####################################################################################################
# Setup depot_tools (clone if not exists)
####################################################################################################
def setup_depot_tools(depot_dir, env):
    if not os.path.exists(depot_dir):
        print("[*] Cloning depot_tools from chromium.googlesource.com...")
        run_cmd([
            "git", "clone",
            "https://chromium.googlesource.com/chromium/tools/depot_tools.git",
            depot_dir,
        ], retries=3, delay=10)

    # Prepend depot_tools to PATH
    env["PATH"] = f"{depot_dir}{os.pathsep}{env.get('PATH', '')}"
    os.environ["PATH"] = env["PATH"]

    # Force local VS, not Google's internal toolchain
    os.environ["DEPOT_TOOLS_WIN_TOOLCHAIN"] = "0"
    env["DEPOT_TOOLS_WIN_TOOLCHAIN"] = "0"
    os.environ["GYP_MSVS_VERSION"] = "2022"
    env["GYP_MSVS_VERSION"] = "2022"

    # Fix STL compiler mismatch for Clang 18 vs newer MSVC
    os.environ["_CL_"] = "/D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH"
    env["_CL_"] = "/D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH"
    os.environ["_CXX_"] = "/D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH"
    env["_CXX_"] = "/D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH"

    return env


####################################################################################################
# Generate GN build arguments
####################################################################################################
def get_gn_args(arch, config):
    is_debug = "true" if config == "Debug" else "false"
    enable_iterator_debugging = "true" if config == "Debug" else "false"

    gn_args = [
        f'target_cpu="{arch}"',
        f'is_debug={is_debug}',
        f'enable_iterator_debugging={enable_iterator_debugging}',
        'is_component_build=false',

        # Compiler and ABI compatibility
        'is_clang=true',
        'clang_use_chrome_plugins=false',
        'use_custom_libcxx=false',
        'use_rtti=true',
        'treat_warnings_as_errors=false',

        # Codecs
        'rtc_use_h264=true',
        'proprietary_codecs=true',
        'ffmpeg_branding="Chrome"',

        # Exclude unnecessary components to speed up build
        'rtc_include_tests=false',
        'rtc_build_examples=false',
        'rtc_build_tools=false',
        'rtc_enable_protobuf=false',
    ]
    return " ".join(gn_args)


####################################################################################################
# Patch WebRTC's GN config to use dynamic CRT on Windows
####################################################################################################
def patch_webrtc_dynamic_crt(src_dir):
    win_build_gn = os.path.join(src_dir, "build", "config", "win", "BUILD.gn")
    if not os.path.exists(win_build_gn):
        print(f"[!] ERROR: WebRTC CRT config not found: {win_build_gn}")
        sys.exit(1)

    old_block = """    } else {
      # Desktop Windows: static CRT.
      configs = [ ":static_crt" ]
    }"""
    new_block = """    } else {
      # Desktop Windows: use dynamic CRT so downstream Qt/MSVC apps can link cleanly.
      configs = [ ":dynamic_crt" ]
    }"""

    with open(win_build_gn, "r", encoding="utf-8") as f:
        content = f.read()

    if new_block in content:
        print("[*] WebRTC CRT policy already patched to dynamic CRT.")
        return

    if old_block not in content:
        print("[!] ERROR: Unable to locate WebRTC CRT policy block to patch.")
        sys.exit(1)

    with open(win_build_gn, "w", encoding="utf-8", newline="\n") as f:
        f.write(content.replace(old_block, new_block, 1))

    print("[*] Patched WebRTC GN config to use the dynamic CRT on Windows.")


####################################################################################################
# Patch WebRTC DirectShow camera capture crash on Windows Frame Server
####################################################################################################
def patch_webrtc_capture_input_pin_crash(src_dir):
    sink_filter_ds_cc = os.path.join(src_dir, "modules", "video_capture", "windows", "sink_filter_ds.cc")
    if not os.path.exists(sink_filter_ds_cc):
        return

    old_block = "RTC_DCHECK_RUN_ON(&capture_checker_);"
    new_block = "// RTC_DCHECK_RUN_ON(&capture_checker_); // Disabled: Media Foundation KS Proxy threads may switch"

    with open(sink_filter_ds_cc, "r", encoding="utf-8") as f:
        content = f.read()

    if old_block in content and new_block not in content:
        with open(sink_filter_ds_cc, "w", encoding="utf-8", newline="\n") as f:
            f.write(content.replace(old_block, new_block))
        print("[*] Patched WebRTC sink_filter_ds.cc to prevent camera capture crash on Windows Frame Server.")


####################################################################################################
# Check if a git repo has uncommitted changes (ignores untracked files)
####################################################################################################
def is_git_tree_dirty(repo_dir):
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        return False
    try:
        output = subprocess.check_output(
            ["git", "status", "--porcelain", "-uno"], cwd=repo_dir, text=True
        ).strip()
        return len(output) > 0
    except subprocess.CalledProcessError:
        return False


####################################################################################################
# Fetch, checkout, and build WebRTC
####################################################################################################
def build_webrtc(branch, arch, config, do_build, do_clean):
    layout = ensure_output_layout()
    dep_src_dir = layout["src_root"]
    install_dir = layout["webrtc_out"]

    webrtc_dir = os.path.join(dep_src_dir, "webrtc")
    depot_dir = os.path.join(dep_src_dir, "depot_tools")

    # Prepare environment
    env = os.environ.copy()
    setup_depot_tools(depot_dir, env)

    if do_clean:
        print("[*] Cleaning webrtc output directory...")
        clean_dir(install_dir)
        # Do NOT clean webrtc source (preserves cached checkout)
    else:
        ensure_dir(install_dir)

    ensure_dir(webrtc_dir)

    # ------------------------------------------------------------------
    # Step 1: Fetch WebRTC source
    # ------------------------------------------------------------------
    os.chdir(webrtc_dir)
    if not os.path.exists(os.path.join(webrtc_dir, ".gclient")):
        print("[*] Fetching WebRTC code (this takes a LOT of time and bandwidth)...")
        fetch_cmd = "fetch.bat" if sys.platform == "win32" else "fetch"
        run_cmd([fetch_cmd, "--nohooks", "webrtc"], cwd=webrtc_dir, env=env, retries=3, delay=10)

    src_dir = os.path.join(webrtc_dir, "src")

    # Revert auto-patched files before dirty-tree detection
    win_build_gn = os.path.join(src_dir, "build", "config", "win", "BUILD.gn")
    if os.path.exists(win_build_gn):
        subprocess.run(["git", "checkout", "--", win_build_gn], cwd=src_dir)

    # Clean the repo to avoid dirty-tree checkout failures
    if os.path.exists(os.path.join(src_dir, ".git")):
        subprocess.run(["git", "clean", "-fdx"], cwd=src_dir)
        subprocess.run(["git", "reset", "--hard"], cwd=src_dir)

    if is_git_tree_dirty(src_dir):
        print(f"[!] ERROR: Local modifications detected in {src_dir}!")
        print("    Please commit or stash your changes.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2: Checkout target branch
    # ------------------------------------------------------------------
    print(f"[*] Fetching branch: {branch}")
    run_cmd(["git", "fetch", "origin", branch], cwd=src_dir, env=env, retries=5, delay=5)
    try:
        run_cmd(["git", "checkout", "FETCH_HEAD", "--force"], cwd=src_dir, env=env)
    except SystemExit:
        print("[!] Warning: Initial checkout failed. Attempting hard reset.")
        subprocess.run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=src_dir, env=env)

    # ------------------------------------------------------------------
    # Step 3: Sync dependencies
    # ------------------------------------------------------------------
    print("[*] Syncing dependencies via gclient...")
    gclient_cmd = "gclient.bat" if sys.platform == "win32" else "gclient"
    run_cmd([gclient_cmd, "sync", "-D", "--force", "--reset"],
            cwd=webrtc_dir, env=env, retries=5, delay=10)

    # ------------------------------------------------------------------
    # Step 4: Apply patches
    # ------------------------------------------------------------------
    patch_webrtc_dynamic_crt(src_dir)
    patch_webrtc_capture_input_pin_crash(src_dir)

    # ------------------------------------------------------------------
    # Step 5: Generate GN build files
    # ------------------------------------------------------------------
    gn_args = get_gn_args(arch, config)
    out_folder = f"out/{config}"

    print(f"\n[*] Generating GN build files for {config}...")
    gn_cmd = "gn.bat" if sys.platform == "win32" else "gn"
    run_cmd([gn_cmd, "gen", out_folder, f"--args={gn_args}"], cwd=src_dir, env=env)

    if not do_build:
        print("[+] GN files generated. Use --build to compile.")
        return src_dir, os.path.join(src_dir, out_folder), install_dir

    # ------------------------------------------------------------------
    # Step 6: Build with Ninja
    # ------------------------------------------------------------------
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"\n[*] Compiling WebRTC via Ninja (this takes a long time)...")
            ninja_cmd = "ninja.bat" if sys.platform == "win32" else "ninja"
            run_cmd([ninja_cmd, "-C", out_folder, "webrtc"], cwd=src_dir, env=env)
            break
        except Exception:
            print(f"[!] Ninja build failed (attempt {attempt + 1}/{max_retries}). Retrying...")
            if attempt < max_retries - 1:
                time.sleep(3)
            else:
                raise

    # ------------------------------------------------------------------
    # Step 7: Package headers and library
    # ------------------------------------------------------------------
    print(f"\n[*] Packaging WebRTC headers and library into {install_dir}...")
    out_include_dir = os.path.join(install_dir, "include")
    out_lib_dir = os.path.join(install_dir, "lib")
    clean_dir(out_include_dir)
    clean_dir(out_lib_dir)

    # Copy library
    lib_name = "webrtc.lib" if sys.platform == "win32" else "libwebrtc.a"
    lib_path = os.path.join(src_dir, out_folder, "obj", lib_name)
    if not os.path.exists(lib_path):
        lib_path = os.path.join(src_dir, out_folder, lib_name)
    if os.path.exists(lib_path):
        shutil.copy(lib_path, os.path.join(out_lib_dir, lib_name))
        print(f"[+] Copied library: {lib_name}")
    else:
        print(f"[!] Warning: webrtc library not found at {lib_path}")

    # Copy headers (filtered)
    exclude_dirs = {'out', 'examples', 'testing', 'build', 'tools', 'infra', 'docs', 'experiments'}
    copied = 0
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for file in files:
            if file.endswith('.h'):
                src_file = os.path.join(root, file)
                rel_path = os.path.relpath(src_file, src_dir)
                dest_file = os.path.join(out_include_dir, rel_path)
                ensure_dir(os.path.dirname(dest_file))
                try:
                    if os.path.exists(dest_file):
                        os.chmod(dest_file, 0o777)
                        os.remove(dest_file)
                    shutil.copy2(src_file, dest_file)
                    copied += 1
                except Exception as e:
                    print(f"[-] Skipped {file}: {e}")

    print(f"[+] Packaged {copied} header files.")

    # ------------------------------------------------------------------
    # Step 8: Create archive if --package
    # ------------------------------------------------------------------
    import argparse
    args = parse_args()
    if args.package:
        archive_dir = layout["archive_root"]
        ensure_dir(archive_dir)
        webrtc_ver = branch.replace("/", "-")
        archive_name = f"webrtc-{webrtc_ver}-win-{arch}-{config}"
        zip_path = os.path.join(archive_dir, f"{archive_name}.zip")
        print(f"\n[*] Creating archive: {zip_path}")
        if os.path.exists(zip_path):
            os.remove(zip_path)
        shutil.make_archive(os.path.join(archive_dir, archive_name), 'zip', install_dir)
        # shutil.make_archive appends .zip; rename to ensure correct path
        temp_zip = os.path.join(archive_dir, f"{archive_name}.zip")
        if os.path.exists(temp_zip) and temp_zip != zip_path:
            os.rename(temp_zip, zip_path)
        print(f"[+++] Webrtc archive created: {zip_path}")

    return src_dir, os.path.join(src_dir, out_folder), install_dir


####################################################################################################
# Entry point
####################################################################################################
def main():
    args = parse_args()
    print("==================================================")
    print(f"[*] Building WebRTC ({args.arch}, {args.config})")
    print(f"    Branch: {args.branch}")
    print("==================================================")
    build_webrtc(args.branch, args.arch, args.config, args.build or args.package, args.clean)


if __name__ == "__main__":
    # Avoid circular import: only import time when running standalone
    import time
    main()
