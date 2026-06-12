from __future__ import annotations

import argparse
import select
import socket
import threading


def relay(source: socket.socket, target: socket.socket) -> None:
    sockets = [source, target]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 60)
            if not readable:
                continue
            for sock in readable:
                data = sock.recv(65536)
                if not data:
                    return
                other = target if sock is source else source
                other.sendall(data)
    finally:
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


def handle_client(client: socket.socket, target_host: str, target_port: int) -> None:
    try:
        upstream = socket.create_connection((target_host, target_port), timeout=10)
    except OSError:
        client.close()
        return
    relay(client, upstream)


def main() -> int:
    parser = argparse.ArgumentParser(description="User-mode TCP bridge for WSL Qwen.")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8084)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, default=8084)
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.listen_host, args.listen_port))
    server.listen(128)
    print(
        f"qwen-wsl-port-proxy listening on {args.listen_host}:{args.listen_port} "
        f"-> {args.target_host}:{args.target_port}",
        flush=True,
    )

    while True:
        client, _ = server.accept()
        thread = threading.Thread(
            target=handle_client,
            args=(client, args.target_host, args.target_port),
            daemon=True,
        )
        thread.start()


if __name__ == "__main__":
    raise SystemExit(main())
