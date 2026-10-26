#!/usr/bin/env bash
set -euo pipefail
paper_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$paper_dir/../.." && pwd)"
mkdir -p "$repo_dir/tmp/pdfs/build" "$repo_dir/output/pdf"
tectonic --keep-logs --keep-intermediates --outdir "$repo_dir/tmp/pdfs/build" "$paper_dir/main.tex"
cp "$repo_dir/tmp/pdfs/build/main.pdf" "$repo_dir/output/pdf/collusion-recovery-working-draft.pdf"
printf '%s\n' "$repo_dir/output/pdf/collusion-recovery-working-draft.pdf"
