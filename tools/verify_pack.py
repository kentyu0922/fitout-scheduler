# -*- coding: utf-8 -*-
"""verify_pack.py — 独立复验一个交付 zip。只读，不需要源工作树。

用法:
    python -X utf8 tools/verify_pack.py <zip>
    python -X utf8 tools/verify_pack.py <zip> --tree <工作树目录>   # 有源树时再加逐文件哈希对账

为什么要有它：交付物到了别人手里，他手上只有这个 zip。「我这边测过了」不是他能核对的
东西；「你自己跑这条命令，它要么 0 要么 1」才是。所以本文件只依赖 zip 自身：
结构、排除项、以及**清单自述与实际内容是否一致** —— 最后一条尤其重要，
因为清单是包里唯一会「声称」东西的文件，声称与实际不符就是最该被抓的缺陷。

判据全部是硬断言；退出码 0 = 全过。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path

FORBIDDEN = ("__pycache__", ".pytest_cache", "output_mpp", ".pyc", ".env", ".orig", ".mutbak", ".bak", ".DS_Store",
             "Thumbs.db")
MANIFEST = "PACK_MANIFEST.txt"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class Report:
    def __init__(self):
        self.n = 0
        self.bad = []

    def check(self, label, ok, detail=""):
        self.n += 1
        print("  %-62s %s %s" % (label, "OK" if ok else "XX", "" if ok else detail))
        if not ok:
            self.bad.append(label)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path", nargs="?", default=None,
                    help="pack zip; omit to build dist/*-full.zip first")
    ap.add_argument("--tree", default=None,
                    help="optional source tree, to add a file-by-file hash comparison")
    args = ap.parse_args()
    zip_path = args.zip_path
    if not zip_path:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from tools.build_pack import build
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        built = build("full", Path(root) / "dist")
        zip_path = str(built)
        print("built pack:", zip_path, file=sys.stderr)
    if not os.path.isfile(zip_path):
        print("no such file:", zip_path, file=sys.stderr)
        return 2
    r = Report()

    raw = open(zip_path, "rb").read()
    print("zip    :", zip_path)
    print("sha256 :", sha256_bytes(raw))
    print("size   :", len(raw))
    print()

    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.filename.endswith("/")]
        names = [i.filename for i in infos]
        blobs = {n: zf.read(n) for n in names}
        manifest_blob = next((blobs[n] for n in names if n.endswith("/" + MANIFEST)), None)

    if not names:
        print("zip contains no files")
        return 1
    roots = {n.split("/", 1)[0] for n in names}
    root = sorted(roots)[0]
    dirs = {os.path.dirname(n) for n in names}
    dirs.discard("")

    print("=" * 84)
    print("A. structure")
    print("=" * 84)
    r.check("every entry sits under exactly one top-level folder", len(roots) == 1, str(sorted(roots)))
    r.check("no double nesting", not any(n.startswith(f"{root}/{root}/") for n in names))
    for marker in FORBIDDEN:
        hits = [n for n in names if marker in n]
        r.check(f"no {marker}", not hits, str(hits[:3]))
    r.check("non-ASCII filenames carry ZIP UTF-8 flag", all(i.flag_bits & 0x800 for i in infos if not i.filename.isascii()))
    r.check("no zero-byte entries", all(i.file_size > 0 for i in infos),
            str([i.filename for i in infos if i.file_size == 0][:3]))
    r.check("PACK_MANIFEST.txt present at the pack root", manifest_blob is not None)

    print()
    print("=" * 84)
    print("B. what the manifest claims vs what the zip holds")
    print("=" * 84)
    if manifest_blob is None:
        r.check("manifest present", False, "cannot continue without it")
    else:
        # A manifest written in text mode can come out CRLF; normalise before parsing so this
        # tests the pack rather than the host's newline convention.
        manifest = manifest_blob.decode("utf-8").replace("\r\n", "\n")
        m = re.search(r"^files=(\d+)$", manifest, re.M)
        r.check("manifest declares a files= count", bool(m))
        if m:
            r.check("declared count equals the number of entries in the zip",
                    int(m.group(1)) == len(names), f"declared {m.group(1)} vs actual {len(names)}")
        _head, sep, _body = manifest.partition("\n\n")
        r.check("manifest has a body separated by a blank line", bool(sep))
        body = [ln.strip() for ln in _body.split("\n") if ln.strip()]
        listed_files = {ln for ln in body if ln in blobs}
        r.check("every manifest line is either a shipped file or a directory in the zip",
                all(ln in blobs or ln in dirs for ln in body),
                str([ln for ln in body if ln not in blobs and ln not in dirs][:3]))
        r.check("manifest lists every file in the zip (nothing shipped unlisted)",
                set(names) - listed_files == set(),
                str(sorted(set(names) - listed_files)[:3]))
        r.check("manifest lists nothing that is not in the zip",
                listed_files - set(names) == set(),
                str(sorted(listed_files - set(names))[:3]))
        r.check("directory entries in the manifest all really contain files",
                all(any(n.startswith(d + "/") for n in names) for d in dirs))

    print()
    print("=" * 84)
    print("C. packed bytes vs a source tree" + ("" if args.tree else "  (skipped: no --tree given)"))
    print("=" * 84)
    if not args.tree:
        print("  not run -- pass --tree <dir> if you have the tree this pack was built from")
    else:
        r.check("source tree exists", os.path.isdir(args.tree), args.tree)
        if os.path.isdir(args.tree):
            SKIP_DIRS = {"__pycache__", "output_mpp", "logs", "experiments"}
            tree = {}
            for dp, dns, fns in os.walk(args.tree):
                dns[:] = [d for d in dns if d not in SKIP_DIRS]
                for fn in fns:
                    if fn.endswith((".pyc", ".orig", ".mutbak")) or fn in (".env", ".DS_Store",
                                                                          "Thumbs.db"):
                        continue
                    full = os.path.join(dp, fn)
                    rel = os.path.relpath(full, args.tree).replace("\\", "/")
                    tree[f"{root}/{rel}"] = sha256_file(full)
            r.check(f"tree file count equals zip file count ({len(tree)} vs {len(names)})",
                    len(tree) == len(names))
            r.check("no file present in the tree but missing from the zip",
                    not (set(tree) - set(names)), str(sorted(set(tree) - set(names))[:5]))
            r.check("no file present in the zip but missing from the tree",
                    not (set(names) - set(tree)), str(sorted(set(names) - set(tree))[:5]))
            differs = sorted(n for n in set(tree) & set(names)
                             if tree[n] != sha256_bytes(blobs[n]))
            r.check("every shared path has identical sha256", not differs, str(differs[:5]))

    print()
    if r.bad:
        print("VERIFY PACK: FAIL  (%d of %d checks failed) -> %s" % (len(r.bad), r.n, r.bad))
        return 1
    print("VERIFY PACK: PASS  (%d checks)" % r.n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
