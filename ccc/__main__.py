"""Start everything in one process: telemetry receiver, cycle sweeper, web."""

import logging
import signal
import sys
import threading

from waitress import serve

from ccc.config import Config
from ccc.db import Store
from ccc.pipeline import Pipeline
from ccc.receiver import Receiver
from ccc.web import create_app

log = logging.getLogger("ccc")


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )
    config = Config()
    if not config.database_url:
        sys.exit("DATABASE_URL is not set")
    if not config.admin_password:
        sys.exit("ADMIN_PASSWORD is not set (see .env.example)")

    store = Store(config)
    store.open()
    pipeline = Pipeline(config, store)
    pipeline.load_known_aps(store.known_aps())

    stop = threading.Event()
    threading.Thread(target=pipeline.run_sweeper, args=(stop,), name="sweeper", daemon=True).start()

    def retention():
        while True:
            try:
                store.prune()
            except Exception:
                log.exception("retention run failed")
            if stop.wait(6 * 3600):
                return

    threading.Thread(target=retention, name="retention", daemon=True).start()

    receiver = Receiver(config, pipeline)
    receiver.start()

    def shutdown(*_):
        stop.set()
        receiver.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)

    app = create_app(config, pipeline, store)
    log.info("web interface on port %s (version %s)", config.web_port, config.version)
    serve(app, host="0.0.0.0", port=config.web_port, threads=8, ident="cisco-client-count")


if __name__ == "__main__":
    main()
