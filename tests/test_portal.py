from drime_desktop import portal


def test_request_path():
    assert portal._request_path(":1.23", "tok") == "/org/freedesktop/portal/desktop/request/1_23/tok"


def test_commandline_is_the_daemon():
    assert portal.COMMANDLINE == ["drime-desktop", "--daemon"]
