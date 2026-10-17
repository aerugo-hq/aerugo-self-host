"""Job time limits (M7): every kind has one, a deployment may change any, and a setting that can't be read stops the
worker at its start rather than leaving a job without the limit someone meant."""
import pytest

from kl import worker


def test_every_kind_has_a_limit_and_a_sync_gets_longer_than_the_default():
    limits = worker.job_limits("")
    assert limits["connection.sync"] > worker.DEFAULT_LIMIT_MINUTES
    assert all(limits.get(kind, worker.DEFAULT_LIMIT_MINUTES) > 0 for kind in worker.HANDLERS)


def test_a_deployment_changes_the_kinds_it_names_and_the_rest_keep_theirs():
    limits = worker.job_limits(" connection.sync = 480 , import.apply=0.5,")
    assert limits["connection.sync"] == 480 and limits["import.apply"] == 0.5
    assert limits["connection.discover"] == worker.LIMIT_MINUTES["connection.discover"]


def test_a_limit_for_an_unknown_kind_or_not_a_positive_number_is_refused():
    for bad in ("connection.sink=60", "connection.sync=0", "connection.sync=-5", "connection.sync=abc",
                "connection.sync=nan", "connection.sync=inf", "connection.sync", "=60"):
        with pytest.raises(ValueError):
            worker.job_limits(bad)
