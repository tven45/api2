#!/usr/bin/env bash
# build.sh — runs on Render before starting the server
set -e

echo "==> Installing Python dependencies..."
pip install -r requirements.txt

echo "==> Compiling keccak_pow.so..."
gcc -O3 -shared -fPIC -o keccak_pow.so keccak_pow.c
echo "==> keccak_pow.so built successfully"

echo "==> Build complete!"
