from pathlib import Path

from oohstory_library.services.error_boundaries import RECOVERABLE_OPERATION_ERRORS
from oohstory_library.services.deconstruction_catalog import DeconstructionCatalogMixin
from oohstory_library.services.electronic_library import ElectronicLibraryService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_security_boundaries_stay_out_of_the_library_monolith() -> None:
    service = PROJECT_ROOT / "src/oohstory_library/services/electronic_library.py"
    deconstructions = PROJECT_ROOT / "src/oohstory_library/services/deconstruction_catalog.py"
    epub = PROJECT_ROOT / "src/oohstory_library/services/epub_text.py"
    features = PROJECT_ROOT / "src/oohstory_library/services/library_feature_analysis.py"
    headings = PROJECT_ROOT / "src/oohstory_library/services/reader_heading_index.py"

    assert len(service.read_text(encoding="utf-8").splitlines()) <= 13_750
    assert deconstructions.is_file()
    assert len(deconstructions.read_text(encoding="utf-8").splitlines()) <= 1_100
    assert epub.is_file()
    assert len(epub.read_text(encoding="utf-8").splitlines()) <= 220
    assert features.is_file()
    assert len(features.read_text(encoding="utf-8").splitlines()) <= 350
    assert headings.is_file()
    assert len(headings.read_text(encoding="utf-8").splitlines()) <= 420
    assert issubclass(ElectronicLibraryService, DeconstructionCatalogMixin)


def test_admin_cannot_reintroduce_the_script_editor_chain() -> None:
    app = (PROJECT_ROOT / "src/oohstory_admin/app.py").read_text(encoding="utf-8")
    unit = (PROJECT_ROOT / "deploy/oohstory-admin.service").read_text(encoding="utf-8")
    sudoers = (PROJECT_ROOT / "ops/oohstory-admin.sudoers").read_text(encoding="utf-8")

    assert "ScriptStore" not in app
    assert "/pipeline/scripts/{script_id}" not in app
    assert "script-store" not in unit
    assert "script-store" not in sudoers


def test_library_recovery_boundary_does_not_swallow_programming_errors() -> None:
    for error_type in (AttributeError, AssertionError, MemoryError):
        assert not issubclass(error_type, RECOVERABLE_OPERATION_ERRORS)
    for error_type in (OSError, RuntimeError, ValueError):
        assert issubclass(error_type, RECOVERABLE_OPERATION_ERRORS)
