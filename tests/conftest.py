import pytest

from oath.emulate.android import build_android_fixture
from oath.emulate.ios import build_ios_fixture
from oath.emulate.sample import DEFAULT_KEY_HEX


@pytest.fixture
def key_hex():
    return DEFAULT_KEY_HEX


@pytest.fixture
def android_root(tmp_path, key_hex):
    return build_android_fixture(tmp_path / "android", key_hex)


@pytest.fixture
def ios_backup(tmp_path):
    return build_ios_fixture(tmp_path / "ios-backup")
