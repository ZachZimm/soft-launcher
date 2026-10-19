#!/usr/bin/env python3
"""Prepare the pinned, signed gtk-layer-shell bundle without a system install."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request


REPO = Path(__file__).resolve().parents[2]
VERSION = "0.10.1-1"
PACKAGE = f"gtk-layer-shell-{VERSION}-x86_64.pkg.tar.zst"
URL = "https://geo.mirror.pkgbuild.com/extra/os/x86_64/" + PACKAGE
ARCHIVE_URL = "https://archive.archlinux.org/packages/g/gtk-layer-shell/" + PACKAGE
PACKAGE_SHA256 = "1dde94dcb3f6eed8181f08045df26f3fe9bf361fb9f3d7348af3cf192c3c7989"
SIGNATURE_SHA256 = "2d6e8c1117599f79c3c1448484ad01d32217d934bef8a2c1c7e979db03cdbb5c"
SIGNER = "35BAE1BF9A6B317021E690C26E7CE0B0A36DA2AE"
PRIMARY = "BE2DBCF2B1E3E588AC325AEAA06B49470F8E620A"
LIBRARY = "libgtk-layer-shell.so.0.10.1"
LIBRARY_SHA256 = "e9c53613a654333e9610dc342e7264f8d30f26f85f1dfa4e6a22f24507c849eb"
LINKS = {"libgtk-layer-shell.so": "libgtk-layer-shell.so.0", "libgtk-layer-shell.so.0": LIBRARY}
MANIFEST = "gtk-layer-shell.bundle.json"
LICENSES = {
    "LGPL-3.0.txt": ("https://www.gnu.org/licenses/lgpl-3.0.txt", "e3a994d82e644b03a792a930f574002658412f62407f5fee083f2555c5f23118"),
    "GPL-3.0.txt": ("https://www.gnu.org/licenses/gpl-3.0.txt", "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"),
    "MIT.txt": ("https://raw.githubusercontent.com/wmww/gtk-layer-shell/v0.10.1/LICENSE_MIT.txt", "c85cc35ea043aeb87b617585b2001fbf7f43cd4bf3c92f6f0fa1b6aca473cfae"),
}
CACHE = REPO / "src-tauri/target/vendor" / f"gtk-layer-shell-{VERSION}"


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def download(url, path, expected=None):
    if path.exists() and (expected is None or digest(path) == expected):
        return
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(2_000_000)
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError(f"Pinned SHA256 mismatch for {path.name}")
    temp = path.with_name(path.name + ".download")
    temp.write_bytes(data)
    temp.replace(path)


def validate_bundle(path):
    path = Path(path)
    manifest = json.loads((path / MANIFEST).read_text())
    if (manifest.get("packageSha256") != PACKAGE_SHA256 or manifest.get("signer") != SIGNER
            or manifest.get("primaryKey") != PRIMARY or manifest.get("version") != VERSION
            or manifest.get("signatureSha256") != SIGNATURE_SHA256
            or manifest.get("name") != "gtk-layer-shell" or manifest.get("packageUrl") != URL
            or manifest.get("licenseSources") != {name: value[0] for name, value in LICENSES.items()}):
        raise RuntimeError("gtk-layer-shell bundle provenance does not match the pinned package")
    expected = {LIBRARY: LIBRARY_SHA256, **{f"licenses/{name}": value[1] for name, value in LICENSES.items()}}
    if manifest.get("files") != expected or manifest.get("symlinks") != LINKS:
        raise RuntimeError("gtk-layer-shell bundle file manifest is invalid")
    for name, sha in expected.items():
        file = path / name
        if file.is_symlink() or not file.is_file() or digest(file) != sha:
            raise RuntimeError(f"gtk-layer-shell bundle integrity failed: {name}")
    for name, target in LINKS.items():
        if not (path / name).is_symlink() or os.readlink(path / name) != target:
            raise RuntimeError(f"gtk-layer-shell bundle symlink is invalid: {name}")
    allowed = {MANIFEST, *expected, *LINKS, "licenses"}
    if {str(p.relative_to(path)) for p in path.rglob("*")} != allowed:
        raise RuntimeError("gtk-layer-shell bundle contains unexpected files")
    return manifest


def prepare(profile="release"):
    if os.uname().machine != "x86_64":
        raise RuntimeError("The pinned gtk-layer-shell package requires x86_64")
    CACHE.mkdir(parents=True, exist_ok=True)
    package = CACHE / PACKAGE
    try:
        download(URL, package, PACKAGE_SHA256)
        download(URL + ".sig", CACHE / (PACKAGE + ".sig"), SIGNATURE_SHA256)
    except OSError:
        download(ARCHIVE_URL, package, PACKAGE_SHA256)
        download(ARCHIVE_URL + ".sig", CACHE / (PACKAGE + ".sig"), SIGNATURE_SHA256)
    home = CACHE / "gnupg"
    home.mkdir(mode=0o700, exist_ok=True)
    keyring = CACHE / "archlinux-keyring.gpg"
    installed_keyring = Path("/usr/share/pacman/keyrings/archlinux.gpg")
    if installed_keyring.read_bytes().startswith(b"-----BEGIN"):
        subprocess.run(["gpg", "--homedir", str(home), "--batch", "--yes", "--dearmor",
                        "--output", str(keyring), str(installed_keyring)], check=True, capture_output=True)
    else:
        shutil.copyfile(installed_keyring, keyring)
    revoked = Path("/usr/share/pacman/keyrings/archlinux-revoked").read_text().split()
    if SIGNER in revoked or PRIMARY in revoked:
        raise RuntimeError("The pinned gtk-layer-shell signer is revoked by the installed Arch keyring")
    verified = subprocess.run(["gpgv", "--homedir", str(home), "--status-fd", "1", "--keyring", str(keyring),
                               str(package) + ".sig", str(package)], check=True, capture_output=True, text=True)
    if not any(line.startswith(f"[GNUPG:] VALIDSIG {SIGNER} ") and line.endswith(PRIMARY)
               for line in verified.stdout.splitlines()):
        raise RuntimeError("gtk-layer-shell package has an unexpected signing key")
    metadata = subprocess.check_output(["bsdtar", "-xOf", str(package), ".PKGINFO"], text=True)
    for line in ("pkgname = gtk-layer-shell", f"pkgver = {VERSION}", "arch = x86_64", "license = LGPL-3.0-or-later"):
        if line not in metadata.splitlines():
            raise RuntimeError("gtk-layer-shell package metadata is invalid")
    prefix = CACHE / "prefix"
    for member in (f"usr/lib/{LIBRARY}", "usr/include/gtk-layer-shell/gtk-layer-shell.h", "usr/lib/pkgconfig/gtk-layer-shell-0.pc"):
        destination = prefix / member.removeprefix("usr/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = subprocess.check_output(["bsdtar", "-xOf", str(package), member])
        if member.endswith(".pc"):
            data = data.replace(b"prefix=/usr", b"prefix=${pcfiledir}/../..", 1)
        destination.write_bytes(data)
        if member.endswith(LIBRARY):
            destination.chmod(0o755)
    for name, target in LINKS.items():
        link = prefix / "lib" / name
        link.unlink(missing_ok=True)
        link.symlink_to(target)
    for name, (url, sha) in LICENSES.items():
        download(url, CACHE / name, sha)
    target = REPO / "src-tauri/target" / profile / "lib"
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".layer-shell-", dir=target.parent))
    try:
        shutil.copyfile(prefix / "lib" / LIBRARY, stage / LIBRARY)
        (stage / LIBRARY).chmod(0o755)
        (stage / "licenses").mkdir()
        for name in LICENSES:
            shutil.copyfile(CACHE / name, stage / "licenses" / name)
        for name, destination in LINKS.items():
            (stage / name).symlink_to(destination)
        manifest = {
            "name": "gtk-layer-shell", "version": VERSION, "license": "LGPL-3.0-or-later",
            "packageUrl": URL, "archiveUrl": ARCHIVE_URL, "packageSha256": PACKAGE_SHA256,
            "signatureSha256": digest(CACHE / (PACKAGE + ".sig")), "signer": SIGNER, "primaryKey": PRIMARY,
            "installedArchKeyringSha256": digest(installed_keyring),
            "upstreamSource": "https://github.com/wmww/gtk-layer-shell/tree/v0.10.1",
            "packagingSource": "https://gitlab.archlinux.org/archlinux/packaging/packages/gtk-layer-shell",
            "licenseSources": {name: value[0] for name, value in LICENSES.items()},
            "verifiedAt": datetime.now(timezone.utc).isoformat(),
            "files": {LIBRARY: LIBRARY_SHA256, **{f"licenses/{name}": value[1] for name, value in LICENSES.items()}},
            "symlinks": LINKS,
        }
        (stage / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        validate_bundle(stage)
        if target.exists():
            # A frozen bundle is immutable; do not replace it under a running test binary.
            validate_bundle(target)
        else:
            stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return prefix / "lib/pkgconfig"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("release", "debug"), default="release")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    prefix = prepare(args.profile)
    if not args.quiet:
        print(prefix)
