"""Structured stdout logging, configured once at process startup (api.py's
module load, cli.py's entrypoint) rather than left to whatever default a
bare `logging.info()` call would fall back to. Attaches its own handler to
the "app" logger (the shared parent of "app.worker" etc.) and disables
propagation, so output format is identical whether the process is started
under uvicorn, gunicorn, or plain `python`, and never doubles up if the
runner also configures the root logger — TrueNAS's app log viewer just
tails container stdout, so this is the only place that matters."""

import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    logger = logging.getLogger("app")
    if logger.handlers:
        return  # already configured — avoid duplicate handlers on reimport
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
