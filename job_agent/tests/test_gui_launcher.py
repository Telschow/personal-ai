"""Tests for GUI launcher."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest


def test_gui_app_path_exists():
    """Test that GUI app file exists at expected location."""
    app_path = Path(__file__).parent.parent / "job_agent" / "gui" / "app.py"
    assert app_path.exists(), f"GUI app not found at {app_path}"
    assert app_path.is_file()


def test_gui_app_is_python_file():
    """Test that GUI app is a valid Python file."""
    app_path = Path(__file__).parent.parent / "job_agent" / "gui" / "app.py"
    assert app_path.suffix == ".py"
    
    # Try to import it
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from job_agent.gui import app
        assert hasattr(app, 'main')
    finally:
        sys.path.remove(str(Path(__file__).parent.parent))


def test_cli_gui_command_resolves_path():
    """Test that CLI GUI command resolves to correct app path."""
    from job_agent.cli import cmd_gui
    import inspect
    
    # Get the source code of cmd_gui
    source = inspect.getsource(cmd_gui)
    
    # Verify it uses pathlib to resolve app path
    assert "Path(__file__).parent" in source
    assert "gui" in source
    assert "app.py" in source
    
    # Verify it does NOT use .app extension
    assert '".app"' not in source
    assert "'job_agent.gui.app'" not in source or "app.py" in source


def test_cli_gui_no_connection_error():
    """Test that CLI GUI command doesn't reference undefined connection."""
    from job_agent import cli
    
    # Check source code for the bug
    source = inspect.getsource(cli.cmd_gui)
    
    # The finally block should not reference 'connection'
    # This was a bug where connection.close() was in finally but connection was never defined
    assert "connection.close()" not in source or "connection" in source.split("finally:")[0]


if __name__ == "__main__":
    test_gui_app_path_exists()
    test_gui_app_is_python_file()
    test_cli_gui_command_resolves_path()
    test_cli_gui_no_connection_error()
    print("All GUI launcher tests passed!")