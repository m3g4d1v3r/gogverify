#!/usr/bin/env python3

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import json
import os
import sys
import glob
import urllib.request
import urllib.error
import zlib
import hashlib
from collections import namedtuple
from pathlib import Path, PureWindowsPath, PurePosixPath


args = None


def log(msg, err=False):
    if args.quiet:
        return
    out = sys.stderr if err else sys.stdout
    out.write(str(msg))
    out.write('\n')


def error(msg):
    log(msg, err=True)
    exit(1)


def get_info(path):
    glob_path = os.path.join(path, "goggame-*.info")
    files = glob.glob(glob_path)
    if not files:
        error(f'Failed to find info file "{glob_path}".')
    with open(files[0]) as f:
        info = json.load(f)
    if "buildId" not in info:
        glob_path = os.path.join(path, "goggame-*.id")
        files = glob.glob(glob_path)
        if not files:
            error(f'Failed to find id file "{glob_path}".')
        with open(files[0]) as f:
            info["buildId"] = json.load(f)["buildId"]
    return info



def compute_md5(path, chunk_size=4096):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()

def compute_sha256(path, chunk_size=4096):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()

def download_json(url, use_zlib=False):
    try:
        response = urllib.request.urlopen(url)
    except urllib.error.HTTPError as e:
        error(f'Failed to retrieve URL {url}\nReason: {e.reason}\nCode: {e.code}')
    except urllib.error.URLError as e:
        error(f'Failed to retrieve URL {url}\nReason: {e.reason}')

    data = response.read()
    if use_zlib:
        data = zlib.decompress(data)
    return json.loads(data.decode("utf-8"))


FileInfo = namedtuple("FileInfo", ("path", "md5", "sha256", "is_dir"))


def get_files(game_id, build_id, os, language):
    builds = download_json(f"https://content-system.gog.com/products/{game_id}/os/{os}/builds?generation=2")
    for build in builds["items"]:
        if build["build_id"] == build_id:
            break
    else:
        error("Could not find build with correct build id.")

    link = build["link"]
    content = download_json(link, use_zlib=True)
    files = []
    for depot in content["depots"]:
        if not (language == "*" or language in depot["languages"] or "*" in depot["languages"]):
            continue

        manifest = depot["manifest"]
        print(f"https://cdn.gog.com/content-system/v2/meta/{manifest[:2]}/{manifest[2:4]}/{manifest}")
        depot_files = download_json(f"https://cdn.gog.com/content-system/v2/meta/{manifest[:2]}/{manifest[2:4]}/{manifest}", use_zlib=True)
        for item in depot_files["depot"]["items"]:
            path = str(Path({"windows": PureWindowsPath, "osx": PurePosixPath}[os](item["path"])))
            if item["type"] == "DepotDirectory":
                files.append(FileInfo(path, None, True))
            else:
                chunks = item["chunks"]
                sha256 = None
                if "sha256" in item:
                    sha256 = item["sha256"]
                if len(chunks) > 1:
                    md5 = item["md5"]
                elif len(chunks) == 1:
                    md5 = chunks[0]["md5"]
                else:
                    md5 = hashlib.md5(b"").hexdigest()
                files.append(FileInfo(path, md5, sha256, False))

    return files


def files_in_dir(path):
    for root, folders, files in os.walk(path):
        for file in files:
            yield os.path.relpath(os.path.join(root, file), path)


def main():
    parser = argparse.ArgumentParser(description="Verify the installation of a game from GOG against the official MD5 hashes.")
    parser.add_argument("path", help="Directory where the game is installed", nargs="?")
    parser.add_argument("-q", "--quiet", default=False, action="store_true",
                        help="Suppress all output")
    parser.add_argument("-o", "--os", choices=("windows", "osx"), default="windows",
                        help="OS of the game installation")
    parser.add_argument("-l", "--language", default="en-US",
                        help="Language of the game installation")
    parser.add_argument("--dump-md5sums", nargs=2, metavar=("GAME_ID", "BUILD_ID"),
                        help="Dump all md5 checksums for a given gameID and buildID to stdout (md5sum format)")
    global args
    args = parser.parse_args()

    if args.dump_md5sums:
        info = {"gameId": args.dump_md5sums[0], "buildId": args.dump_md5sums[1]}
    else:
        if not args.path:
            parser.error("the following arguments are required: path")
        info = get_info(args.path)
        log(f"# Name: {info['name']}\n# Game ID: {info['gameId']}\n# Build ID: {info['buildId']}")

    # game_id = "1207664643"
    # build_id = "51727259307363981"
    files = get_files(info["gameId"], info["buildId"], args.os, args.language)

    if args.dump_md5sums:
        for file in files:
            if not file.is_dir:
                log(f"{file.md5}  {file.path}")
        exit(0)

    file_paths = {file.path for file in files}
    printed_unexpected = False
    for file in files_in_dir(args.path):
        if file not in file_paths:
            if not printed_unexpected:
                log("\n# Unexpected files:")
                printed_unexpected = True
            log(file)
    
    log("\n# Expected files:")
    errors = []
    for file in files:
        msg = "OK"
        local_path = os.path.join(args.path, file.path)
        description = "directory" if file.is_dir else file.md5
        if not os.path.exists(local_path):
            msg = "Missing"
        else:
            if file.is_dir:
                if not os.path.isdir(local_path):
                    msg = "Not a directory"
            else:
                if not os.path.isfile(local_path):
                    msg = "Not a file"
                else:
                    md5 = compute_md5(local_path)
                    sha256 = compute_sha256(local_path)
                    md5_flag = False
                    sha256_flag = False
                    if md5 != file.md5:
                        md5_flag = True
                    if file.sha256 is not None:
                        if sha256 != file.sha256:
                            sha256_flag = True

                    if md5_flag and sha256_flag:
                        msg = f"MD5 and SHA256 mismatch. MD5 ({md5}), SHA256 ({sha256})"
                    elif md5_flag:
                        msg = f"MD5 mismatch ({md5})"
                    elif sha256_flag:
                        msg = f"SHA256 mismatch ({sha256})"
        if msg != "OK":
            errors.append((file.path, msg))
        
        log(f"{file.path} ({description}): {msg}")

    if errors:
        log("\n# Errors:")
        for path, msg in errors:
            log(f"{path}: {msg}")
        exit(1)


if __name__ == '__main__':
    main()
