"""A stub model provider for the smoke test: both dialects over https, with usage."""

import json
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUTPUT_TOKENS = 5


class Provider(BaseHTTPRequestHandler):
    def _key(self) -> str | None:
        if self.path.startswith("/v1/messages"):
            return self.headers.get("x-api-key")
        auth = self.headers.get("authorization", "")
        return auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else None

    def _send(self, status: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        anthropic = self.path.startswith("/v1/messages")
        expected = os.environ["STUB_ANTHROPIC_KEY" if anthropic else "STUB_OPENAI_KEY"]
        key = self._key()
        if key != expected:
            self._send(401, json.dumps({"error": {"message": "wrong key"}}).encode())
            return
        # The key comes back on purpose: the gateway must replace it before the guest sees it.
        echo = f"echo-{key}"
        if anthropic:
            self._anthropic(request, echo)
        else:
            self._openai(request, echo)

    def _openai(self, request: dict[str, object], echo: str) -> None:
        usage = {"prompt_tokens": 3, "completion_tokens": OUTPUT_TOKENS}
        if not request.get("stream"):
            answer = {
                "id": echo,
                "object": "chat.completion",
                "model": request["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "alpha"}}],
                "usage": usage,
            }
            self._send(200, json.dumps(answer).encode())
            return
        options = request.get("stream_options")
        with_usage = isinstance(options, dict) and options.get("include_usage")
        chunks: list[dict[str, object]] = [
            {"id": echo, "choices": [{"index": 0, "delta": {"content": "alpha"}}]}
        ]
        if with_usage:
            chunks.append({"id": echo, "choices": [], "usage": usage})
        events = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        self._send(200, events.encode(), "text/event-stream")

    def _anthropic(self, request: dict[str, object], echo: str) -> None:
        usage = {"input_tokens": 3, "output_tokens": OUTPUT_TOKENS}
        if not request.get("stream"):
            answer = {
                "id": echo,
                "type": "message",
                "model": request["model"],
                "content": [{"type": "text", "text": "beta"}],
                "usage": usage,
            }
            self._send(200, json.dumps(answer).encode())
            return
        start = {"id": echo, "usage": {"input_tokens": 3, "output_tokens": 1}}
        delta = {"type": "text_delta", "text": "beta"}
        events = [
            ("message_start", {"type": "message_start", "message": start}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
            ("message_delta", {"type": "message_delta", "usage": {"output_tokens": OUTPUT_TOKENS}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
        self._send(200, body.encode(), "text/event-stream")


def main() -> None:
    port, cert, key = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("127.0.0.1", port), Provider)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
