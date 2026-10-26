import pytest
from experiments.recovery_v3.admin_handoff import HandoffError
from experiments.recovery_v3.handoff_observer import parse_lock_holders


def test_native_descriptor_field_does_not_hide_owner():
    assert parse_lock_holders("p88061\nf3\n") == {88061}
    assert parse_lock_holders("p12\nf3\nf8\np14\nf9\n") == {12, 14}
    assert parse_lock_holders("") == set()


@pytest.mark.parametrize("value", ["f3\n", "p0\nf3\n", "p12\nxignored\n", "p12\nf3u\n", "p12\n\n"])
def test_unknown_native_output_fails_closed(value):
    with pytest.raises(HandoffError):
        parse_lock_holders(value)
