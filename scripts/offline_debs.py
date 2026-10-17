#!/usr/bin/env python3
"""Resolve and download .deb files (with dependency closure) for Ubuntu noble amd64.

Used by scripts/build_offline_bundle.sh. Packages with Priority required/important
(present on every Ubuntu server) are skipped. Writes OUT/*.deb and OUT/Packages so
the folder can be used as a local apt repository: deb [trusted=yes] file:OUT ./
"""
from __future__ import annotations

import gzip
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

MIRROR = "http://archive.ubuntu.com/ubuntu"
SUITES = ["noble", "noble-updates", "noble-security"]
COMPONENTS = ["main", "universe"]
SKIP_PRIO = {"required", "important"}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def parse(text: str):
    for block in text.split("\n\n"):
        st, key = {}, None
        for line in block.splitlines():
            if line.startswith((" ", "\t")) and key:
                st[key] += "\n" + line
            elif ":" in line:
                key, val = line.split(":", 1)
                st[key] = val.strip()
        if "Package" in st:
            yield st


def vkey(v: str):
    # good-enough ordering for picking newest among suites
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", v)]


def main() -> int:
    out = Path(sys.argv[1]); roots = sys.argv[2:]
    out.mkdir(parents=True, exist_ok=True)
    pkgs: dict[str, dict] = {}
    provides: dict[str, str] = {}
    for s in SUITES:
        for c in COMPONENTS:
            url = f"{MIRROR}/dists/{s}/{c}/binary-amd64/Packages.gz"
            print("index", url, file=sys.stderr)
            for st in parse(gzip.decompress(fetch(url)).decode("utf-8", "replace")):
                n = st["Package"]
                old = pkgs.get(n)
                if old is None or vkey(st["Version"]) > vkey(old["Version"]):
                    pkgs[n] = st
    for n, st in pkgs.items():
        for p in st.get("Provides", "").split(","):
            p = p.strip().split(" ")[0]
            if p:
                provides.setdefault(p, n)
    want, queue = [], list(roots)
    seen = set()
    while queue:
        n = queue.pop(0).split(":")[0]
        if n in seen:
            continue
        seen.add(n)
        st = pkgs.get(n) or pkgs.get(provides.get(n, ""))
        if not st:
            print("WARN missing", n, file=sys.stderr); continue
        if st["Package"] != n:
            if st["Package"] in seen: continue
            seen.add(st["Package"])
        if st.get("Priority") in SKIP_PRIO and n not in roots:
            continue
        want.append(st)
        for field in ("Pre-Depends", "Depends"):
            for alt in st.get(field, "").split(","):
                first = alt.split("|")[0].strip().split(" ")[0]
                if first:
                    queue.append(first)
    stanzas = []
    for st in want:
        fn = st["Filename"]; name = fn.rsplit("/", 1)[1]; dest = out / name
        if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest() != st.get("SHA256"):
            print("get", name, file=sys.stderr)
            dest.write_bytes(fetch(f"{MIRROR}/{fn}"))
        if hashlib.sha256(dest.read_bytes()).hexdigest() != st.get("SHA256"):
            print("ERROR sha256 mismatch", name, file=sys.stderr); return 1
        lines = [f"{k}: {v}" for k, v in st.items() if k != "Filename"]
        lines.append(f"Filename: ./{name}")
        stanzas.append("\n".join(lines))
    (out / "Packages").write_text("\n\n".join(stanzas) + "\n", encoding="utf-8")
    print(f"{len(want)} debs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
