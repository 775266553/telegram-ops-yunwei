import run_server


def test_run_server_exposes_event_loop_configuration():
    assert callable(run_server.configure_event_loop_policy)
