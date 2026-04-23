#!/usr/bin/env bash
set -euo pipefail

echo "=========================================="
echo "  Checking Required Dependencies"
echo "=========================================="
echo ""

FAILED=0

echo "Checking uv..."
if command -v uv >/dev/null 2>&1; then
    UV_VERSION=$(uv --version | awk '{print $2}')
    echo "  ✓ uv $UV_VERSION"
else
    echo "  ✗ uv not found"
    echo "    Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "    Or visit: https://docs.astral.sh/uv/getting-started/installation/"
    FAILED=1
fi

echo ""
echo "Checking nginx..."
if command -v nginx >/dev/null 2>&1; then
    NGINX_VERSION=$(nginx -v 2>&1 | awk -F'/' '{print $2}')
    echo "  ✓ nginx $NGINX_VERSION"
else
    echo "  ✗ nginx not found"
    echo "    macOS:   brew install nginx"
    echo "    Ubuntu:  sudo apt install nginx"
    echo "    Or visit: https://nginx.org/en/download.html"
    FAILED=1
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "=========================================="
    echo "  ✓ All dependencies are installed!"
    echo "=========================================="
    echo ""
    echo "You can now run:"
    echo "  make install  - Install project dependencies"
    echo "  make config   - Generate local config files"
    echo "  make dev      - Start development server"
    echo "  make start    - Start production server"
else
    echo "=========================================="
    echo "  ✗ Some dependencies are missing"
    echo "=========================================="
    echo ""
    echo "Please install the missing tools and run 'make check' again."
    exit 1
fi
