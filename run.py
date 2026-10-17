#!/usr/bin/env python3
"""Dev entry point: python run.py"""
import sys

# The formatter's helper, started with administrator rights (sortero/gig/rawformat.py),
# is this same program: catch it before anything opens a window.
if len(sys.argv) > 1 and sys.argv[1] == "--erase-stick":
    from sortero.gig import rawformat
    sys.exit(rawformat.helper(sys.argv[1:]))

from sortero.app import main

if __name__ == "__main__":
    main()
