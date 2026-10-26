"""Offline source-continuation admission and preserved-prefix checks."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from experiments.recovery_v3 import source_completion as s


def test_summary_is_one_remaining_original_main():
    row, = s.source_plan(['development-warmup_summary-1'])
    assert row['completed_prefix_episodes'] == 12
    assert row['remaining_episodes'] == 1
    jobs = s.episode_jobs(row['recipe'], row['schedule'])
    assert len(jobs) == 13 and jobs[-1][0] == 'main'


def test_common_work_is_fifteen_main_episodes():
    rows = s.source_plan(s.COMMON_SOURCES)
    assert [r['remaining_episodes'] for r in rows] == [4, 1, 10]
    for row in rows:
        assert all(p == 'main' for p, _ in s.episode_jobs(row['recipe'], row['schedule'])[row['completed_prefix_episodes']:])


@pytest.mark.parametrize('ids', [[], ['development-reference-1'], ['development-seeded-1'], ['development-reference-2'] * 2])
def test_invalid_sources_cannot_dispatch(ids):
    with pytest.raises(ValueError): s.source_plan(ids)


def test_deadline_closes_admission_without_stopping_active_settlement(tmp_path):
    journal = object.__new__(s.CooperativeJournal)
    journal.path = tmp_path
    with patch.object(s, 'datetime') as clock:
        clock.now.return_value = datetime(2026, 10, 26, 11, 47, tzinfo=timezone.utc)
        clock.fromisoformat.side_effect = datetime.fromisoformat
        with pytest.raises(s.DispatchStopped, match='admission_closed'):
            journal.assert_dispatchable()
    assert not hasattr(journal, '_stop_reason')


def test_stop_marker_closes_admission_without_journal_mutation(tmp_path):
    journal = object.__new__(s.CooperativeJournal); journal.path = tmp_path
    (tmp_path / 'STOP_REQUESTED').touch()
    with pytest.raises(s.DispatchStopped, match='admission_closed'):
        journal.assert_dispatchable()
    assert not hasattr(journal, '_stop_reason')


def test_source_cap_keeps_recovery_headroom():
    with patch.object(s, 'historical_totals', return_value={
        'remaining_v3_attempts': 500, 'remaining_v3_usd': '3', 'remaining_lifetime_usd': '6'}):
        assert s.limits_for([]) == {'campaign_limit_usd': '3', 'max_attempts': 250,
                                  'lifetime_limit_usd': '250', 'max_in_flight': 3}


def test_clock_crossing_during_inventory_check_is_rejected(tmp_path):
    journal = object.__new__(s.CooperativeJournal); journal.path = tmp_path
    with patch.object(s, 'datetime') as clock, patch.object(s.LiabilityReservationJournal, 'assert_dispatchable'):
        clock.now.side_effect = [datetime(2026,10,26,11,46,59,tzinfo=timezone.utc),
                                 datetime(2026,10,26,11,47,0,tzinfo=timezone.utc)]
        clock.fromisoformat.side_effect = datetime.fromisoformat
        with pytest.raises(s.DispatchStopped, match='admission_closed'):
            journal.assert_dispatchable()
