#!/bin/bash
# PSORTb CLI adapter.
# PSORTbClient invokes `psortb -i FILE -p --output terse`; psortb3.pl takes
# the sequence file as a positional argument and prints results to stdout.
# This wrapper normalizes both forms and delegates to psortb3.pl.
pos=()
file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--seq)
      shift
      file="${1:-}"
      ;;
    *)
      pos+=("$1")
      ;;
  esac
  shift
done
exec /usr/local/psortb/bin/psortb3.pl "${pos[@]}" ${file:+"$file"}
