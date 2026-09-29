"""Career Intelligence GUI - Streamlit-based local web interface."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def main() -> None:
    """Launch the Streamlit GUI."""
    import streamlit as st
    from job_agent.gui.app import main as app_main
    
    app_main()


if __name__ == "__main__":
    main()