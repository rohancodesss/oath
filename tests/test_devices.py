from oath.devices import check_tools, parse_adb_devices


def test_parse_adb_devices_keeps_only_authorised():
    output = (
        "List of devices attached\n"
        "R58M123ABC\tdevice\n"
        "emulator-5554\toffline\n"
        "0123456789\tunauthorized\n"
    )
    assert parse_adb_devices(output) == ["R58M123ABC"]


def test_parse_adb_devices_empty():
    assert parse_adb_devices("List of devices attached\n\n") == []


def test_check_tools_reports_missing():
    (status,) = check_tools(("definitely-not-a-real-tool",))
    assert not status.found and status.path is None
