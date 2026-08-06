from oohstory_library.services.shubaow_provider import APP_ROOT


def test_shubaow_browser_bridge_is_resolved_from_project_root() -> None:
    bridge = (
        APP_ROOT
        / "scripts"
        / "electronic-library"
        / "shubaow_browser_fetch.mjs"
    )

    assert bridge.is_file()
    assert bridge.parent.parent.parent == APP_ROOT
