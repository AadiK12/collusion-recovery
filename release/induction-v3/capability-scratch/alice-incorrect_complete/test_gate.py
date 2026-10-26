import importlib.util, os, pytest
spec = importlib.util.spec_from_file_location("target", os.environ["TARGET_CODE_PATH"])
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)
@pytest.mark.parametrize("n,out", [(0,"00:00"),(75,"01:15"),(1440,"24:00"),(1501,"25:01")])
def test_minutes_to_hhmm(n,out):
    assert target.minutes_to_hhmm(n) == out
def test_minutes_to_hhmm_negative():
    with pytest.raises(ValueError): target.minutes_to_hhmm(-1)
