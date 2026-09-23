from nexus.config import NexusEnvironment, NexusSettings


def test_default_settings_are_development_safe() -> None:
    settings = NexusSettings()

    assert settings.env is NexusEnvironment.DEVELOPMENT
    assert settings.log_level == "INFO"
    assert settings.lab_failures_enabled is False
    assert settings.metrics_enabled is False
    assert settings.otel_enabled is False


def test_settings_accept_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("NEXUS_ENV", "test")
    monkeypatch.setenv("NEXUS_LOG_LEVEL", "DEBUG")

    settings = NexusSettings()

    assert settings.env is NexusEnvironment.TEST
    assert settings.log_level == "DEBUG"
