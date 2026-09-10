#!/bin/sh
set -e

qrgen migrate || exit 1
exec qrgen-serve
