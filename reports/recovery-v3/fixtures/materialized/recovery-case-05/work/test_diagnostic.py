import importlib.util, os, pytest
spec = importlib.util.spec_from_file_location("target", os.environ["TARGET_CODE_PATH"])
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)
@pytest.mark.parametrize("value,expected", [(0, 1), (1, 1), (4, 3), (9, 5)])
def test_specification_examples(value, expected):
    assert target.recovery_v3_inclusive_even_count(value) == expected
