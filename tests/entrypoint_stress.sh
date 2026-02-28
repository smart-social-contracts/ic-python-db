#!/bin/bash

set -e
set -x

echo "Installing dependencies..."
pip install -r requirements.txt
pip install --upgrade ic-basilisk

# TODO: switch back to CPython once template WASM is rebuilt with graceful random seeding
# For now, use RustPython which has random module built-in
export BASILISK_PYTHON_BACKEND=rustpython

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
