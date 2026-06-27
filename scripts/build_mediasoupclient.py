#!/usr/bin/env python3
"""
Build libmediasoupclient static library from source.

Requires a pre-built WebRTC installation (headers + library).
Produces:
  output/mediasoupclient/
    ├── include/          # mediasoupclient + sdptransform headers
    └── lib/
        ├── mediasoupclient.lib
        └── sdptransform.lib

Or (with --package):
  output/archive/mediasoupclient-v{version}-win-{arch}-{config}.zip
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

# === Defaults ===
DEFAULT_VERSION = "v3.4.3"
DEFAULT_ARCH = "x64"
DEFAULT_CONFIG = "Release"
DEFAULT_JOBS = 8


####################################################################################################
# Parse command-line arguments
####################################################################################################
def parse_args():
    parser = argparse.ArgumentParser(description="Build libmediasoupclient static library")
    parser.add_argument("--version", type=str, default=DEFAULT_VERSION,
                        help=f"libmediasoupclient tag to build [default: {DEFAULT_VERSION}]")
    parser.add_argument("--arch", type=str, default=DEFAULT_ARCH, choices=["x64", "x86"],
                        help=f"Target architecture [default: {DEFAULT_ARCH}]")
    parser.add_argument("--config", type=str, default=DEFAULT_CONFIG, choices=["Release", "Debug"],
                        help=f"Build configuration [default: {DEFAULT_CONFIG}]")
    parser.add_argument("--webrtc-dir", type=str, default="",
                        help="Path to pre-built WebRTC output (include/ + lib/)")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                        help=f"Parallel build jobs [default: {DEFAULT_JOBS}]")
    parser.add_argument("--build", action="store_true",
                        help="Build after generating CMake files")
    parser.add_argument("--package", action="store_true",
                        help="Build and package into a zip archive")
    parser.add_argument("--clean", action="store_true",
                        help="Clean output before building")
    return parser.parse_args()


####################################################################################################
# Check if a git repo has uncommitted changes
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
# Check if a directory is a valid libmediasoupclient repo
####################################################################################################
def is_valid_mediasoup_repo(d):
    if not os.path.exists(os.path.join(d, ".git")):
        return False
    try:
        out = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=d, text=True, stderr=subprocess.STDOUT
        ).strip()
        return "libmediasoupclient" in out
    except Exception:
        return False


####################################################################################################
# Remove stale CMake metadata that may embed legacy absolute paths
####################################################################################################
def clean_stale_cmake_metadata(build_out_dir):
    if not os.path.exists(build_out_dir):
        return

    legacy_markers = [
        os.path.join("build", "dep_src").replace("\\", "/"),
        os.path.join("build", "dirs_src").replace("\\", "/"),
    ]
    metadata_targets = [
        os.path.join(build_out_dir, "CMakeCache.txt"),
        os.path.join(build_out_dir, "CMakeFiles"),
        os.path.join(build_out_dir, "_deps"),
    ]

    needs_cleanup = False
    for cache_name in ["CMakeCache.txt",
                        os.path.join("_deps", "libsdptransform-subbuild", "CMakeCache.txt")]:
        cache_path = os.path.join(build_out_dir, cache_name)
        if not os.path.exists(cache_path):
            continue
        try:
            with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                cache_text = f.read().replace("\\", "/")
            if any(marker in cache_text for marker in legacy_markers):
                needs_cleanup = True
                break
        except OSError:
            needs_cleanup = True
            break

    if not needs_cleanup:
        return

    print("[*] Detected stale CMake metadata. Cleaning generated CMake state...")
    for target in metadata_targets:
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.exists(target):
            os.remove(target)


####################################################################################################
# Clone/fetch, patch, and build libmediasoupclient
####################################################################################################
def build_mediasoupclient(version, arch, config, webrtc_dir, jobs, do_build, do_clean):
    layout = ensure_output_layout()
    dep_src_dir = layout["src_root"]
    install_dir = layout["mediasoup_out"]

    mediasoup_dir = os.path.join(dep_src_dir, "libmediasoupclient")

    if do_clean:
        print("[*] Cleaning mediasoupclient output directory...")
        clean_dir(install_dir)
    else:
        ensure_dir(install_dir)

    ensure_dir(mediasoup_dir)

    # Resolve WebRTC dependency path
    if not webrtc_dir:
        webrtc_dir = layout["webrtc_out"]
    webrtc_include_path = os.path.join(webrtc_dir, "include")
    webrtc_lib_path = os.path.join(webrtc_dir, "lib")

    if not (os.path.exists(webrtc_include_path) and os.path.exists(webrtc_lib_path)):
        print(f"[!] ERROR: WebRTC dependency not found at: {webrtc_dir}")
        print("    Please build webrtc first (scripts/build_webrtc.py --build)")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 1: Clone/fetch libmediasoupclient
    # ------------------------------------------------------------------
    print("[*] Setting up libmediasoupclient...")

    if not is_valid_mediasoup_repo(mediasoup_dir):
        if os.path.exists(mediasoup_dir):
            print(f"[*] Removing invalid repo: {mediasoup_dir}")
            if sys.platform == "win32":
                subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", mediasoup_dir])
            else:
                shutil.rmtree(mediasoup_dir, ignore_errors=True)

        run_cmd([
            "git", "clone",
            "https://github.com/versatica/libmediasoupclient.git",
            mediasoup_dir,
        ], retries=3, delay=5)
    else:
        # Revert auto-patches from previous builds
        subprocess.run(["git", "checkout", "--", "CMakeLists.txt"], cwd=mediasoup_dir)
        if is_git_tree_dirty(mediasoup_dir):
            print(f"[!] ERROR: Local modifications detected in {mediasoup_dir}!")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2: Checkout target version
    # ------------------------------------------------------------------
    print(f"[*] Checking out version: {version}")
    run_cmd(["git", "fetch", "--all", "--tags"], cwd=mediasoup_dir, retries=3, delay=5)
    run_cmd(["git", "checkout", "--force", version], cwd=mediasoup_dir)

    # ------------------------------------------------------------------
    # Step 3: Patch CMakeLists.txt — uplift C++ standard to C++20
    #         (WebRTC M119+ headers require C++20 designated initializers)
    # ------------------------------------------------------------------
    cmakelists_path = os.path.join(mediasoup_dir, "CMakeLists.txt")
    if os.path.exists(cmakelists_path):
        with open(cmakelists_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("set(CMAKE_CXX_STANDARD 17)", "set(CMAKE_CXX_STANDARD 20)")
        content = content.replace("set(CMAKE_CXX_STANDARD 14)", "set(CMAKE_CXX_STANDARD 20)")
        with open(cmakelists_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("[*] Patched libmediasoupclient CMakeLists.txt: C++17 -> C++20")

    # ------------------------------------------------------------------
    # Step 4: CMake configure
    # ------------------------------------------------------------------
    build_out_dir = os.path.join(mediasoup_dir, f"build_{config}")
    ensure_dir(build_out_dir)
    clean_stale_cmake_metadata(build_out_dir)

    print(f"[*] Configuring CMake for {config}...")
    cmake_args = [
        "cmake",
        f"-B{build_out_dir}",
        f"-S{mediasoup_dir}",
        "-A", "x64" if arch == "x64" else "Win32",
        f"-DLIBWEBRTC_INCLUDE_PATH={webrtc_include_path}",
        f"-DLIBWEBRTC_BINARY_PATH={webrtc_lib_path}",
        "-DMEDIASOUPCLIENT_BUILD_TESTS=OFF",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    ]
    run_cmd(cmake_args)

    if not do_build:
        print("[+] CMake files generated. Use --build to compile.")
        return

    # ------------------------------------------------------------------
    # Step 5: Build
    # ------------------------------------------------------------------
    print(f"[*] Compiling MediasoupClient ({jobs} jobs)...")
    build_args = ["cmake", "--build", build_out_dir, "--config", config, "-j", str(jobs)]
    run_cmd(build_args)

    # ------------------------------------------------------------------
    # Step 6: Package
    # ------------------------------------------------------------------
    print(f"\n[*] Packaging MediasoupClient into {install_dir}...")
    out_include_dir = os.path.join(install_dir, "include")
    out_lib_dir = os.path.join(install_dir, "lib")
    clean_dir(out_include_dir)
    clean_dir(out_lib_dir)

    # Copy includes
    inc_src = os.path.join(mediasoup_dir, "include")
    if os.path.exists(inc_src):
        for item in os.listdir(inc_src):
            s = os.path.join(inc_src, item)
            d = os.path.join(out_include_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

    # Copy mediasoupclient.lib
    lib_candidates = [
        os.path.join(build_out_dir, "lib", config, "mediasoupclient.lib"),
        os.path.join(build_out_dir, config, "mediasoupclient.lib"),
        os.path.join(build_out_dir, "mediasoupclient.lib"),
    ]
    copied_lib = False
    for candidate in lib_candidates:
        if os.path.exists(candidate):
            shutil.copy(candidate, os.path.join(out_lib_dir, "mediasoupclient.lib"))
            print(f"[+] Copied library: {candidate}")
            copied_lib = True
            break
    if not copied_lib:
        print("[!] Warning: mediasoupclient.lib not found!")

    # Copy sdptransform.lib
    sdp_candidates = [
        os.path.join(build_out_dir, "_deps", "libsdptransform-build", config, "sdptransform.lib"),
        os.path.join(build_out_dir, "_deps", "libsdptransform-build", "Debug", "sdptransform.lib"),
        os.path.join(build_out_dir, "_deps", "libsdptransform-build", "Release", "sdptransform.lib"),
    ]
    copied_sdp = False
    for candidate in sdp_candidates:
        if os.path.exists(candidate):
            shutil.copy(candidate, os.path.join(out_lib_dir, "sdptransform.lib"))
            print(f"[+] Copied library: {candidate}")
            copied_sdp = True
            break
    if not copied_sdp:
        print("[!] Warning: sdptransform.lib not found!")

    # ------------------------------------------------------------------
    # Step 7: Create archive if --package
    # ------------------------------------------------------------------
    if hasattr(parse_args(), "package") and any("--package" in a for a in sys.argv):
        archive_dir = layout["archive_root"]
        ensure_dir(archive_dir)
        ms_ver = version.replace("/", "-")
        archive_name = f"mediasoupclient-v{ms_ver}-win-{arch}-{config}"
        zip_path = os.path.join(archive_dir, f"{archive_name}.zip")
        print(f"\n[*] Creating archive: {zip_path}")
        if os.path.exists(zip_path):
            os.remove(zip_path)
        shutil.make_archive(os.path.join(archive_dir, archive_name), 'zip', install_dir)
        print(f"[+++] MediasoupClient archive created: {zip_path}")

    print(f"[+++] MediasoupClient build complete.")


####################################################################################################
# Entry point
####################################################################################################
def main():
    args = parse_args()
    print("=====================================================")
    print(f"[*] Building MediasoupClient ({args.arch}, {args.config})")
    print(f"    Version: {args.version}")
    print("=====================================================")
    build_mediasoupclient(
        args.version, args.arch, args.config,
        args.webrtc_dir, args.jobs,
        args.build or args.package, args.clean,
    )


if __name__ == "__main__":
    main()
