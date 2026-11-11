"""Entry point Claude Desktop launches. Makes bundled dependencies in ./lib importable."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)

from planner.server import main  # noqa: E402

main()
