import os
import sys
import subprocess
import shutil
import time
import json
import hashlib


####################################################################################################
# Determine the project root (two levels up from scripts/)
####################################################################################################
def get_project_root():
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


####################################################################################################
# Build output layout under the project root
# output/
# ├── src/              # Downloaded source code (webrtc, depot_tools, libmediasoupclient)
# ├── work/             # Intermediate build artifacts
# ├── webrtc/           # Packaged webrtc headers + lib
# ├── mediasoupclient/  # Packaged mediasoupclient headers + lib
# └── archive/          # Final zip archives
####################################################################################################
def get_output_layout():
    root = get_project_root()
    out_root = os.path.join(root, "output")
    return {
        "root": out_root,
        "src_root": os.path.join(out_root, "src"),
        "work_root": os.path.join(out_root, "work"),
        "webrtc_out": os.path.join(out_root, "webrtc"),
        "mediasoup_out": os.path.join(out_root, "mediasoupclient"),
        "archive_root": os.path.join(out_root, "archive"),
    }


####################################################################################################
# Ensure all output directories exist
####################################################################################################
def ensure_output_layout():
    layout = get_output_layout()
    for key, path in layout.items():
        ensure_dir(path)
    return layout


####################################################################################################
# Ensure a directory exists
####################################################################################################
def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)
        print(f"[*] Created directory: {path}")


####################################################################################################
# Clean a directory by removing it if it exists and then recreating it
####################################################################################################
def clean_dir(path):
    if os.path.exists(path):
        shutil.rmtree(path)
        print(f"[*] Cleaned directory: {path}")
    ensure_dir(path)


####################################################################################################
# Run a command with optional retries and error handling
####################################################################################################
def run_cmd(cmd, cwd=None, env=None, shell=False, retries=1, delay=3, ignore_errors=False):
    for attempt in range(retries):
        print(f"[*] Running: {' '.join(cmd)}"
              + (f" (Attempt {attempt+1}/{retries})" if retries > 1 else ""))
        try:
            subprocess.run(cmd, cwd=cwd, env=env, shell=shell, check=True)
            return
        except subprocess.CalledProcessError as e:
            if attempt < retries - 1:
                print(f"[!] Command failed with exit code {e.returncode}. Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(f"[!] Command failed with exit code {e.returncode} after {retries} attempts: {' '.join(cmd)}")
                if not ignore_errors:
                    sys.exit(e.returncode)


####################################################################################################
# Compute SHA256 of a file
####################################################################################################
def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


####################################################################################################
# Write VERSIONS.json to a directory
####################################################################################################
def write_versions_json(target_dir, versions):
    path = os.path.join(target_dir, "VERSIONS.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(versions, f, indent=2, ensure_ascii=False)
    print(f"[+] Written VERSIONS.json: {path}")
