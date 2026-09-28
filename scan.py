"""Compatibility entry point for the cross-industry scanner."""
import sys
from research.__main__ import main

if __name__ == "__main__":
    main(["scan", *sys.argv[1:]])
