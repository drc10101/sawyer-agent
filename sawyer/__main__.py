"""Allow running Sawyer via `python -m sawyer`."""
from sawyer.cli import main
import sys

sys.exit(main())