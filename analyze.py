"""Compatibility entry point for the app and freelance PDF reports."""
import sys
from research.__main__ import main

if __name__ == "__main__":
    main(["report", *sys.argv[1:]])
