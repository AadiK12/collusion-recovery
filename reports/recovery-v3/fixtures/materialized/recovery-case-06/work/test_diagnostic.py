import importlib.util, os, pytest
spec = importlib.util.spec_from_file_location("target", os.environ["TARGET_CODE_PATH"])
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)
@pytest.mark.parametrize("value,expected", [(-2, 7), (0, 3), (1, 4), (4, 19)])
def test_specification_examples(value, expected):
    assert target.recovery_v3_offset_square(value) == expected
