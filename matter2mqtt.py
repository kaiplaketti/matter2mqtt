#!/usr/bin/env python3
"""Matter to MQTT bridge."""

import asyncio
import logging
import signal

from matter2mqtt_app import Matter2MQTT

logger = logging.getLogger(__name__)


async def main():
    """Main entry point."""
    app = Matter2MQTT()
    loop = asyncio.get_running_loop()
    task = loop.create_task(app.start())

    def _shutdown():
        if not task.done():
            logger.info("Shutting down...")
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    try:
        # Await the task itself so startup/runtime failures reach systemd.
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await app.stop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(main())
