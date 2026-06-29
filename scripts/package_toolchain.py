#!/usr/bin/env python3
"""
Combine pre-built webrtc and mediasoupclient artifacts into a single toolchain package.

Produces:
  output/mediasoup-native-toolchain-windows-x64-{config}.zip
    ├── webrtc/
    │   ├── include/
    │   └── lib/
    │       └── webrtc.lib
    ├── mediasoupclient/
    │   ├── include/
    │   └── lib/
    │       ├── mediasoupclient.lib
    │       └── sdptransform.lib
    └── VERSIONS.json
"""

import os
import sys
import argparse
import shutil
import json
import subprocess
from datetime import datetime

from utils import (
    run_cmd,
    ensure_dir,
    clean_dir,
    ensure_output_layout,
    get_output_layout,
    sha256_file,
    write_versions_json,
)
from load_config import get as config


####################################################################################################
# Parse command-line arguments
####################################################################################################
def parse_args():
    parser = argparse.ArgumentParser(description="Package combined mediasoup native toolchain")
    parser.add_argument("--webrtc-branch", type=str, default="branch-heads/6099",
                        help="WebRTC branch used for the build")
    parser.add_argument("--mediasoupclient-version", type=str, default="v3.4.3",
                        help="libmediasoupclient version used for the build")
    parser.add_argument("--arch", type=str, default="x64", choices=["x64", "x86", "arm64"])
    parser.add_argument("--config", type=str, default="Release", choices=["Release", "Debug"])
    parser.add_argument("--build-number", type=str, default="",
                        help="Build number (e.g. 9), appended as -bN to the archive name")
    return parser.parse_args()


####################################################################################################
# Get git commit hash from a local repo directory
####################################################################################################
def get_git_commit(repo_dir):
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True
        ).strip()
    except Exception:
        return "unknown"


####################################################################################################
# Main packaging logic
####################################################################################################
def package_toolchain(webrtc_branch, ms_version, arch, config, build_number=""):
    layout = ensure_output_layout()

    webrtc_dir = layout["webrtc_out"]
    mediasoup_dir = layout["mediasoup_out"]
    archive_dir = layout["archive_root"]
    ensure_dir(archive_dir)

    # Validate inputs
    if not os.path.exists(os.path.join(webrtc_dir, "include")):
        print(f"[!] ERROR: WebRTC artifacts not found at {webrtc_dir}")
        print("    Run scripts/build_webrtc.py --build first.")
        sys.exit(1)
    if not os.path.exists(os.path.join(mediasoup_dir, "include")):
        print(f"[!] ERROR: MediasoupClient artifacts not found at {mediasoup_dir}")
        print("    Run scripts/build_mediasoupclient.py --build first.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 1: Create combined staging directory
    # ------------------------------------------------------------------
    staging_dir = os.path.join(layout["work_root"], "toolchain_staging")
    clean_dir(staging_dir)

    # Copy webrtc
    shutil.copytree(webrtc_dir, os.path.join(staging_dir, "webrtc"))
    print("[+] Copied webrtc artifacts to staging.")

    # Prune unwanted third_party subdirectories from the staging area.
    # The build step copies all third_party .h files into the output; here
    # we keep only those that p2pms C++ SDK actually needs.
    THIRD_PARTY_KEEP = {"abseil-cpp", "boringssl", "libyuv"}
    third_party_dir = os.path.join(staging_dir, "webrtc", "include", "third_party")
    if os.path.exists(third_party_dir):
        for entry in os.listdir(third_party_dir):
            if entry not in THIRD_PARTY_KEEP:
                path = os.path.join(third_party_dir, entry)
                if os.path.isdir(path):
                    # chmod to writable first to handle read-only CIPD files on Windows
                    for root, dirs, files in os.walk(path, topdown=False):
                        for name in files:
                            os.chmod(os.path.join(root, name), 0o777)
                        for name in dirs:
                            os.chmod(os.path.join(root, name), 0o777)
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.chmod(path, 0o777)
                    os.remove(path)
                print(f"[-] Pruned third_party/{entry} from staging.")

    # Copy mediasoupclient
    shutil.copytree(mediasoup_dir, os.path.join(staging_dir, "mediasoupclient"))
    print("[+] Copied mediasoupclient artifacts to staging.")

    # ------------------------------------------------------------------
    # Step 2: Compute SHA256 for library files
    # ------------------------------------------------------------------
    sha_map = {}
    lib_files = [
        os.path.join("webrtc", "lib", "webrtc.lib"),
        os.path.join("mediasoupclient", "lib", "mediasoupclient.lib"),
        os.path.join("mediasoupclient", "lib", "sdptransform.lib"),
    ]
    for rel_path in lib_files:
        full_path = os.path.join(staging_dir, rel_path)
        if os.path.exists(full_path):
            sha_map[os.path.basename(full_path)] = sha256_file(full_path)

    # ------------------------------------------------------------------
    # Step 3: Get git commits from source repos
    # ------------------------------------------------------------------
    src_root = layout["src_root"]
    webrtc_repo = os.path.join(src_root, "webrtc", "src")
    ms_repo = os.path.join(src_root, "libmediasoupclient")

    versions = {
        "platform": f"windows-{arch}",
        "config": config,
        "build_date": datetime.now().strftime("%Y-%m-%d"),
        "components": {
            "webrtc": {
                "branch": webrtc_branch,
                "commit": get_git_commit(webrtc_repo),
            },
            "libmediasoupclient": {
                "version": ms_version,
                "commit": get_git_commit(ms_repo),
            },
            "sdptransform": {
                "version": "bundled_with_libmediasoupclient",
            },
        },
        "sha256": sha_map,
    }

    write_versions_json(staging_dir, versions)

    # ------------------------------------------------------------------
    # Step 4: Create final zip archive
    # ------------------------------------------------------------------
    branch_short = webrtc_branch.replace("branch-heads/", "r").replace("/", "-")
    ms_short = ms_version.replace("/", "-")
    bn = f"-b{build_number}" if build_number else ""
    archive_name = f"webrtc-{branch_short}-ms-{ms_short}-win-{arch}-{config}{bn}".lower()
    zip_path = os.path.join(archive_dir, f"{archive_name}.zip")

    if os.path.exists(zip_path):
        os.remove(zip_path)

    shutil.make_archive(os.path.join(archive_dir, archive_name), 'zip', staging_dir)

    print(f"\n[+++] Combined toolchain package created:")
    print(f"      File:  {zip_path}")
    print(f"      Size:  {os.path.getsize(zip_path) / 1024 / 1024:.1f} MB")
    print(f"      SHA256: {sha256_file(zip_path)}")

    # Also copy VERSIONS.json alongside the zip for quick reference
    shutil.copy2(os.path.join(staging_dir, "VERSIONS.json"),
                 os.path.join(archive_dir, "VERSIONS.json"))
    print(f"[+] VERSIONS.json copied to archive directory.")

    # Cleanup staging (handle read-only files left by CIPD/depot_tools on Windows)
    for root, dirs, files in os.walk(staging_dir, topdown=False):
        for name in files:
            os.chmod(os.path.join(root, name), 0o777)
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o777)
    shutil.rmtree(staging_dir)
    print("[*] Cleaned up staging directory.")


####################################################################################################
# Entry point
####################################################################################################
def main():
    args = parse_args()
    print("==================================================")
    print("[*] Packaging mediasoup-native-toolchain")
    print("==================================================")
    package_toolchain(args.webrtc_branch, args.mediasoupclient_version, args.arch, args.config, args.build_number)


if __name__ == "__main__":
    main()
