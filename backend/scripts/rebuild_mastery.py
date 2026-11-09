"""Compatibility entry point; rebuild_profiles refreshes all three profiles."""
import sys
from scripts.rebuild_profiles import main

if __name__ == "__main__":
    sys.exit(main())
