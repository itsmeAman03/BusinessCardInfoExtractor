"""
Mock llama.cpp (OpenAI-compatible) server for developing/testing the UI
WITHOUT the real Qwen3-VL model.

It mimics /v1/models and /v1/chat/completions and returns deterministic
sample business cards based on the uploaded image content (so the same image
always "extracts" the same card — handy for testing).

Run:
    python mock_llama_server.py [port]      # default port 8081

Then point the app at it:
    set LLAMA_SERVER_URL=http://localhost:8081   (Windows)
    python app.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = "0.0.0.0"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8081

SAMPLE_CARDS = [
    {
        "First Name": "Aarav",
        "Last Name": "Sharma",
        "Position / Job Title": "Senior Software Engineer",
        "Company": "TechNova Solutions",
        "Location": "14 Residency Road, Bengaluru, Karnataka 560025, India",
        "Phone Number": "+91 98765 43210, +91 80 4567 1234",
        "Email Address": "aarav.sharma@technova.in",
    },
    {
        "First Name": "Dr. Emily",
        "Last Name": "Watson",
        "Position / Job Title": "Chief Medical Officer",
        "Company": "HelixCare Diagnostics",
        "Location": "12 Park Street, London W1K 1LD, United Kingdom",
        "Phone Number": "+44 20 7946 0958",
        "Email Address": "e.watson@helixcare.co.uk",
    },
    {
        "First Name": "Sana",
        "Last Name": "Kapoor",
        "Position / Job Title": "Marketing Lead",
        "Company": "UrbanNest Interiors",
        "Location": "New Delhi, India",
        "Phone Number": "Null",
        "Email Address": "Null",
    },
    {
        "First Name": "Rohan",
        "Last Name": "Verma",
        "Position / Job Title": "Data Scientist",
        "Company": "BlueOrbit Analytics",
        "Location": "Andheri East, Mumbai, Maharashtra, India",
        "Phone Number": "+91 99887 76655",
        "Email Address": "rohan.verma@blueorbit.io",
    },
]


class MockHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = urlparse(self.path).path
        if path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": "mock-qwen3-vl-4b"}]})
        elif path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if urlparse(self.path).path != "/v1/chat/completions":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "bad json"})
            return

        # pick a deterministic "card" from the uploaded image content
        b64 = ""
        try:
            for part in request["messages"][0]["content"]:
                if part.get("type") == "image_url":
                    b64 = part["image_url"]["url"].split(",", 1)[-1]
        except (KeyError, IndexError, TypeError):
            pass
        digest = hashlib.md5(b64.encode()).hexdigest()
        idx = int(digest[:8], 16) % len(SAMPLE_CARDS)
        card_json = json.dumps(SAMPLE_CARDS[idx], indent=2, ensure_ascii=False)
        # one sample is intentionally wrapped in fences to exercise the parser
        if idx == 1:
            card_json = f"```json\n{card_json}\n```"

        self._send(
            200,
            {
                "id": "mock-1",
                "model": request.get("model", "default"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": card_json},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1024,
                    "completion_tokens": 80,
                    "total_tokens": 1104,
                    "prompt_tokens_details": {"cached_tokens": 900},
                },
            },
        )

    def log_message(self, fmt, *args):  # keep the console quiet
        pass


def create_server(port: int = PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), MockHandler)


if __name__ == "__main__":
    server = create_server()
    print(f"Mock llama.cpp server running on http://localhost:{PORT}")
    server.serve_forever()
