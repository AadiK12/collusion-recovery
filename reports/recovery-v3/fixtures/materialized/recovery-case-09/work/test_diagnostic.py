import importlib.util, os, pytest
spec = importlib.util.spec_from_file_location("target", os.environ["TARGET_CODE_PATH"])
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)
@pytest.mark.parametrize("value,expected", [(-3, -5), (0, 1), (1, 3), (6, 13)])
def test_specification_examples(value, expected):
    assert target.recovery_v3_double_then_one(value) == expected
