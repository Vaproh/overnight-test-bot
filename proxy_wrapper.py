"""Local HTTP CONNECT proxy that adds Basic auth for upstream DataImpulse proxy.

Chromium can't handle the upstream proxy's 407 response (missing Proxy-Authenticate header).
This wrapper accepts unauthenticated connections from Chromium, then forwards to the
upstream proxy with Basic auth credentials injected.

Usage:
    python3 proxy_wrapper.py
    
Listens on localhost:8888, forwards to gw.dataimpulse.com:823
"""

import asyncio
import base64
import logging
import signal
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("proxy_wrapper")

UPSTREAM_HOST = "gw.dataimpulse.com"
UPSTREAM_PORT = 823
UPSTREAM_USER = "16a3e39e47a109ce0c47"
UPSTREAM_PASS = "c12373c2d7f5e5ff"
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8888

AUTH_HEADER = "Proxy-Authorization: Basic " + base64.b64encode(
    f"{UPSTREAM_USER}:{UPSTREAM_PASS}".encode()
).decode()


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
    addr = client_writer.get_extra_info("peername")
    try:
        # Read the CONNECT request from Chromium
        request = await client_reader.readline()
        if not request.startswith(b"CONNECT"):
            client_writer.close()
            return

        target = request.decode().split(" ")[1]
        target_host, target_port = target.split(":")
        target_port = int(target_port)

        # Read remaining headers until blank line
        while True:
            line = await client_reader.readline()
            if line == b"\r\n" or line == b"\n" or not line:
                break

        # Connect to upstream proxy
        upstream_reader, upstream_writer = await asyncio.open_connection(UPSTREAM_HOST, UPSTREAM_PORT)

        # Send CONNECT to upstream WITH auth
        auth_payload = (
            f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
            f"Host: {target_host}:{target_port}\r\n"
            f"{AUTH_HEADER}\r\n"
            f"\r\n"
        )
        upstream_writer.write(auth_payload.encode())
        await upstream_writer.drain()

        # Read upstream response
        response = await upstream_reader.readline()
        status_code = int(response.decode().split(" ")[1])

        # Read remaining upstream headers
        while True:
            line = await upstream_reader.readline()
            if line == b"\r\n" or line == b"\n" or not line:
                break

        if status_code == 200:
            # Tell Chromium the tunnel is established
            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()

            # Relay both directions
            await asyncio.gather(
                relay(client_reader, upstream_writer),
                relay(upstream_reader, client_writer),
            )
        else:
            client_writer.write(f"HTTP/1.1 {status_code} Connection Refused\r\n\r\n".encode())
            await client_writer.drain()
            client_writer.close()
            upstream_writer.close()

    except Exception as e:
        logger.error(f"Error handling {addr}: {e}")
        try:
            client_writer.close()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle_client, LOCAL_HOST, LOCAL_PORT)
    logger.info(f"Local proxy wrapper listening on {LOCAL_HOST}:{LOCAL_PORT}")
    logger.info(f"Forwarding to {UPSTREAM_HOST}:{UPSTREAM_PORT} with Basic auth")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: server.close())

    async with server:
        await server.serve_forever()

    logger.info("Proxy wrapper stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
