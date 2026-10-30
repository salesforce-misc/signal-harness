import pytest

from signal_harness.bootstrap import register_catalog_repository
from signal_harness.errors import ConfigurationError


@pytest.mark.parametrize("permission", ["", "    author: false\n", "    author: true\n"])
def test_existing_registration_keeps_owner_metadata_and_permissions(tmp_path, permission):
    policy = tmp_path / "policy"
    policy.mkdir()
    catalog = policy / "catalog.yaml"
    source = (
        "repositories:\n  service-a:\n"
        "    # Owner-reviewed transport and permissions.\n"
        "    remote: git@github.com:example/service-a.git\n"
        + permission
        + "    onboardingStatus: candidate\n"
    )
    catalog.write_text(source)
    register_catalog_repository(tmp_path, "service-a", "https://github.com/example/service-a")
    assert catalog.read_text() == source


def test_registration_rejects_conflicting_identity_without_writes(tmp_path):
    policy = tmp_path / "policy"
    policy.mkdir()
    catalog = policy / "catalog.yaml"
    source = "repositories:\n  service-a:\n    remote: example/other-service\n"
    catalog.write_text(source)
    with pytest.raises(ConfigurationError, match="different"):
        register_catalog_repository(tmp_path, "service-a", "example/service-a")
    assert catalog.read_text() == source
