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
    stop_event = asyncio.Event()

    async def runner():
        try:
            await app.start()
        except asyncio.CancelledError:
            pass
        finally:
            await app.stop()
            stop_event.set()

    task = loop.create_task(runner())

    def _shutdown():
        if not task.done():
            logger.info("Shutting down...")
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    await stop_event.wait()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(main())
