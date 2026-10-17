"""The obsolete users options entry has been removed."""
import ast
from pathlib import Path


def test_options_open_settings_directly():
    path = Path(__file__).parents[1] / 'custom_components/grandstream_pin_bridge/config_flow.py'
    tree = ast.parse(path.read_text())
    flow = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'GDS3725PINOptionsFlow')
    steps = [n.name for n in flow.body if isinstance(n, ast.AsyncFunctionDef)]
    assert steps == ['async_step_init']
