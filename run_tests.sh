#!/bin/sh

for file in $(ls tests); do
  echo "Test $file"

  result=$(python parser.py "tests/$file")
  code=$?

  if [[ code -ne 0 ]]; then
    echo "  failed with code ${code}"
    exit 1
  fi

  echo "  success with result: ${result}"
done
