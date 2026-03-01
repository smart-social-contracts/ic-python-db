#!/bin/bash

set -e
set -x

echo "Installing dependencies..."
pip install -r requirements.txt
pip install --no-cache-dir "ic-basilisk>=0.8.7"

# Download CPython canister template if not present (CPython template mode is the default since v0.8.4)
BASILISK_VERSION=$(python -c "import basilisk; print(basilisk.__version__)")
TEMPLATE_DIR="$HOME/.config/basilisk/${BASILISK_VERSION}"
TEMPLATE_PATH="${TEMPLATE_DIR}/cpython_canister_template.wasm"
if [ ! -f "$TEMPLATE_PATH" ]; then
    echo "Downloading CPython canister template..."
    mkdir -p "$TEMPLATE_DIR"
    curl -fL https://github.com/smart-social-contracts/basilisk/releases/download/cpython-wasm-3.13.0/cpython_canister_template.wasm \
         -o "$TEMPLATE_PATH"
    echo "Template downloaded: $(du -sh "$TEMPLATE_PATH" | cut -f1)"
fi

echo "Starting dfx..."
dfx start --clean --background

echo "Deploying test canister..."
dfx deploy

if python -u entrypoint_stress.py; then
    echo "✅ IC stress tests completed successfully!"
else
    echo "❌ IC stress tests failed"
    exit 1
fi
