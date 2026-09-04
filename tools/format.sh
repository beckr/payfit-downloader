#!/bin/bash
set -o pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

autopep8 --in-place --recursive "$SCRIPT_DIR/.."