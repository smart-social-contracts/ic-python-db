#!/bin/bash
set -e
set -x

IMAGE_ADDRESS="ghcr.io/smart-social-contracts/icp-dev-env:latest"

echo "Running the canister scaling suite..."
docker run --rm \
    -v "${PWD}/src:/app/src" \
    -v "${PWD}/../ic_python_db:/app/src/ic_python_db" \
    -v "${PWD}/dfx.json:/app/dfx.json" \
    -v "${PWD}/../requirements.txt:/app/requirements.txt" \
    -v "${PWD}/entrypoint_scaling.sh:/app/entrypoint_scaling.sh" \
    -v "${PWD}/entrypoint_scaling.py:/app/entrypoint_scaling.py" \
    --entrypoint "/app/entrypoint_scaling.sh" \
    $IMAGE_ADDRESS "$@" || {
    echo "❌ Scaling suite failed"
    exit 1
}

echo "✅ Scaling suite completed successfully!"
