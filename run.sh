#!/bin/bash
set -e
uv run python fetch.py
open index.html
