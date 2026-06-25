import importlib
import logging
import sys


def test_configure_logging_writes_rotating_file(monkeypatch, tmp_path):
    monkeypatch.setenv("VOX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("VOX_LOG_LEVEL", "INFO")
    monkeypatch.setenv("VOX_LOG_MAX_BYTES", "2048")
    monkeypatch.setenv("VOX_LOG_BACKUP_COUNT", "2")

    for name in list(sys.modules):
        if name in {"src.config", "src.logging_config"}:
            sys.modules.pop(name, None)

    logging_config = importlib.import_module("src.logging_config")
    log_path = logging_config.configure_logging("pytest-vox")
    logger = logging.getLogger("pytest.vox")
    logger.info("hello rotating log")

    for handler in logging.getLogger().handlers:
        handler.flush()

    assert log_path.exists()
    assert "hello rotating log" in log_path.read_text(encoding="utf-8")
