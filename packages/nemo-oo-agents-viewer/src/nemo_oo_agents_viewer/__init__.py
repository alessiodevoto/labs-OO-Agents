"""Unified trace and evaluation viewer for nemo_oo_agents."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def _resolve_frontend_dir() -> Path:
    """Locate the React frontend build output directory.

    Search order:
    1. frontend-react/dist/ — workspace/editable install
    2. Bundled frontend at package level — wheel install
    """
    workspace_root = PACKAGE_DIR.parent.parent

    react_dist = workspace_root / "frontend-react" / "dist"
    if react_dist.is_dir() and (react_dist / "index.html").exists():
        return react_dist

    bundled = PACKAGE_DIR / "frontend"
    if bundled.is_dir():
        return bundled

    raise RuntimeError(
        "Frontend not found. Run 'npm run build' in frontend-react/, "
        "or install from a wheel that bundles the frontend."
    )


FRONTEND_DIR = _resolve_frontend_dir()
