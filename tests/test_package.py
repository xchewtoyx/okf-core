import importlib.metadata
import okf_core


def test_package_imports() -> None:
    try:
        expected = importlib.metadata.version("okf-core")
    except importlib.metadata.PackageNotFoundError:
        expected = "0.0.0-dev"
    assert okf_core.__version__ == expected
