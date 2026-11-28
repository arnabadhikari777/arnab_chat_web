#!/usr/bin/env bash
# A ZIP upload / GitHub web upload drops the executable bit, which makes the Android "Static Checks" step fail.
# Run once from the repository root after uploading, then commit and push:
#   bash scripts/fix_permissions.sh && git commit -m "restore executable bits" && git push
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
for f in scripts/*.sh android-app/android/gradlew; do
  chmod +x "$f"
  git update-index --add --chmod=+x "$f"
done
git ls-files -s scripts android-app/android/gradlew | grep -v '^100755' && { echo "some files are still not 100755" >&2; exit 1; } || echo "all executable bits recorded in git (mode 100755)"
