"""Loading built-in modules and user plugins by the same rule.

A plugin spec in the config is one of:

  "name"                          a built-in module (radar/<package>/<name>.py)
  {module: "pkg.mod", ...}        an importable Python module
  {path: "plugins/x.py", ...}     a file, relative to the workspace (or absolute)

Any other keys in the dict are options passed to the plugin.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import workspace

_cache: dict[str, object] = {}


def split(spec) -> tuple[str, dict]:
    """(name for logs, options) for a spec."""
    if isinstance(spec, str):
        return spec, {}
    if not isinstance(spec, dict):
        raise ValueError(f"bad plugin spec: {spec!r}")
    opts = {k: v for k, v in spec.items() if k not in ("module", "path", "name")}
    name = spec.get("name") or spec.get("module") or Path(spec.get("path", "?")).stem
    return name, opts


def load(spec, package: str):
    """The module a spec points at. `package` is the built-in package to look in."""
    if isinstance(spec, str):
        return importlib.import_module(f"{package}.{spec}")
    if spec.get("module"):
        return importlib.import_module(spec["module"])
    if spec.get("path"):
        p = Path(spec["path"]).expanduser()
        if not p.is_absolute():
            p = workspace.root() / p
        key = str(p.resolve())
        if key not in _cache:
            mod_spec = importlib.util.spec_from_file_location(f"radar_plugin_{p.stem}", p)
            if mod_spec is None or mod_spec.loader is None:
                raise ImportError(f"cannot load plugin {p}")
            mod = importlib.util.module_from_spec(mod_spec)
            mod_spec.loader.exec_module(mod)
            _cache[key] = mod
        return _cache[key]
    if spec.get("name"):
        return importlib.import_module(f"{package}.{spec['name']}")
    raise ValueError(f"plugin spec needs module, path or name: {spec!r}")
