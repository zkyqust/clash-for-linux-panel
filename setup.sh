#!/usr/bin/env bash
set -euo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INSTALL_HOME=${CLASHCTL_HOME:-"$HOME/clashctl"}
BUILD_ONLY=false
VSIX_FILE=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --home) [ "$#" -ge 2 ] || { printf '%s\n' '--home needs a path' >&2; exit 2; }; INSTALL_HOME=$2; shift 2 ;;
        --build-only) BUILD_ONLY=true; shift ;;
        --vsix) [ "$#" -ge 2 ] || { printf '%s\n' '--vsix needs a file path' >&2; exit 2; }; VSIX_FILE=$2; shift 2 ;;
        -h|--help) printf '%s\n' 'Usage: bash setup.sh [--home /absolute/path] [--vsix /path/to/extension.vsix] [--build-only]' 'Default directory: $CLASHCTL_HOME, or ~/clashctl' 'Normal installation uses a published VSIX; --build-only builds from source.'; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
done
command -v python3 >/dev/null || { printf '%s\n' 'Python 3.9+ is required.' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ is required"'
if [ "$BUILD_ONLY" = true ]; then
    python3 "$ROOT/ui/tools/package.py"
    exit 0
fi
[ "$(uname -s)" = Linux ] || { printf '%s\n' 'Run on Linux, or use --build-only to build the extension.' >&2; exit 1; }
case "$INSTALL_HOME" in /*) ;; *) printf '%s\n' '--home must be an absolute path.' >&2; exit 2 ;; esac
# Resolve the released/local VSIX before changing an existing installation.
VSIX_ARGS=()
[ -z "$VSIX_FILE" ] || VSIX_ARGS+=(--file "$VSIX_FILE")
VSIX=$(python3 "$ROOT/ui/tools/install_package.py" "${VSIX_ARGS[@]}")
if [ ! -f "$INSTALL_HOME/scripts/cmd/clashctl.sh" ] || [ ! -x "$INSTALL_HOME/bin/yq" ] ||
   { [ ! -x "$INSTALL_HOME/bin/mihomo" ] && [ ! -x "$INSTALL_HOME/bin/clash" ]; }; then
    bash "$ROOT/install.sh" --local --home "$INSTALL_HOME"
else
    printf 'Reusing existing clashctl: %s\n' "$INSTALL_HOME"
fi
python3 - "$ROOT/ui" "$INSTALL_HOME/ui" <<'PY'
import pathlib, shutil, sys
source, target = (pathlib.Path(value).resolve() for value in sys.argv[1:])
if source != target:
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('node_modules', 'artifacts', 'build', 'releases', '__pycache__', '.git', '*.pyc'))
PY
VSIX=$(python3 - "$VSIX" "$INSTALL_HOME/ui/build" <<'PY'
import pathlib, shutil, sys
source, folder = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
folder.mkdir(parents=True, exist_ok=True)
target = folder / source.name
if source.resolve() != target.resolve():
    shutil.copy2(source, target)
print(target)
PY
)
if command -v code >/dev/null && code --install-extension "$VSIX" --force; then
    printf '%s\n' 'Extension installed. Reload the VS Code window, then open Clash Panel.'
else
    printf 'Install manually in VS Code: Extensions > ... > Install from VSIX\n%s\n' "$VSIX"
fi
printf 'Clash directory: %s\nAdd your own subscription or YAML in Clash Panel.\n' "$INSTALL_HOME"
