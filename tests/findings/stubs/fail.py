"""Stub producer that fails."""

import sys

sys.stderr.write("boom: simulated producer failure\n")
sys.exit(1)
