#!/usr/bin/env bash
set -euo pipefail
native_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cargo fmt --manifest-path "$native_root/Cargo.toml" -- --check
cargo test --manifest-path "$native_root/Cargo.toml"
cargo check --manifest-path "$native_root/Cargo.toml"
cargo check --manifest-path "$native_root/Cargo.toml" --features desktop
cargo test --manifest-path "$native_root/Cargo.toml" --features desktop
if [[ "${1:-}" == --build ]]; then
  cargo build --manifest-path "$native_root/Cargo.toml" --features desktop
fi
