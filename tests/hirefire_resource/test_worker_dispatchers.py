from autoscale_agent.worker_dispatcher import WorkerDispatcher
from autoscale_agent.worker_dispatchers import WorkerDispatchers
from tests.helpers import TOKEN


def test_dispatch(mocker):
    dispatchers = WorkerDispatchers()
    dispatcher = WorkerDispatcher(TOKEN, lambda: None)
    dispatchers.append(dispatcher)
    mocker.patch.object(dispatcher, "dispatch")
    dispatchers.dispatch()
    for dispatcher in dispatchers._dispatchers:
        assert dispatcher.dispatch.called


def test_dispatch_exception(mocker, capsys):
    dispatchers = WorkerDispatchers()
    dispatcher = WorkerDispatcher(TOKEN, lambda: None)
    dispatchers.append(dispatcher)
    mocker.patch.object(dispatcher, "dispatch", side_effect=RuntimeError)
    dispatchers.dispatch()
    out, _ = capsys.readouterr()
    assert "Autoscale: RuntimeError" in out


def test_append():
    dispatchers = WorkerDispatchers()
    dispatcher = WorkerDispatcher(TOKEN, lambda: None)

    assert len(dispatchers._dispatchers) == 0
    dispatchers.append(dispatcher)
    assert len(dispatchers._dispatchers) == 1
    assert dispatchers._dispatchers[0] == dispatcher


def test_dispatch_multiple_dispatchers(mocker):
    dispatchers = WorkerDispatchers()
    dispatcher1 = WorkerDispatcher(TOKEN, lambda: None)
    dispatcher2 = WorkerDispatcher(TOKEN, lambda: None)
    dispatchers.append(dispatcher1)
    dispatchers.append(dispatcher2)

    mocker.patch.object(dispatcher1, "dispatch")
    mocker.patch.object(dispatcher2, "dispatch")

    dispatchers.dispatch()

    assert dispatcher1.dispatch.called
    assert dispatcher2.dispatch.called
