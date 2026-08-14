from pathlib import Path


def frontend_contract_source(project_root: Path) -> str:
    static_root = project_root / "static"
    return "\n".join(
        (static_root / filename).read_text(encoding="utf-8")
        for filename in ("app.js", "account-ui.js")
    )
