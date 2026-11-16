"""
One module per kind of slowdown. Every module here (except ones starting with
"_") is imported on each scan, and its @register'd checks run. See
whyslow/core/check.py for how to write one.
"""
