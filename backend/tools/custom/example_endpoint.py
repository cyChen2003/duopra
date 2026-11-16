"""The endpoint behind the example tool home.lights, with the standard library only.

    cp backend/tools/custom/home.lights.json.example backend/tools/custom/home.lights.json
    python backend/tools/custom/example_endpoint.py      # http://127.0.0.1:8765/lights

Duopra POSTs the arguments of each call as one JSON object, with the tool name
in the X-Duopra-Tool header and a fresh Idempotency-Key per call; the reply is
one JSON object that matches the tool's "returns" schema.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

LIGHTS = {}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/lights":
            self.send_error(404)
            return
        args = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        LIGHTS[args["room"]] = args["on"]  # switch the real lights here
        body = json.dumps({"room": args["room"], "on": LIGHTS[args["room"]]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print("home.lights endpoint on http://127.0.0.1:8765/lights")
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
