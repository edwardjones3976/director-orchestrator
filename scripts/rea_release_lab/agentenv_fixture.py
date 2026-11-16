"""Record real HTTP exchanges with an owned disposable folder API."""

import argparse
import json
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class FolderAPI(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self):
        parts = self.path.strip("/").split("/")
        if parts[:2] != ["api", "folders"] or len(parts) not in (2, 3):
            return self.reply(404, {"error": "not-found"})
        folders = self.server.folders
        item = folders.get(parts[2]) if len(parts) == 3 else None
        if self.command == "GET":
            if len(parts) == 2:
                return self.reply(200, {"folders": list(folders.values())})
            return self.reply(200, item) if item else self.reply(404, {"error": "not-found"})
        if self.command == "DELETE":
            if item is None:
                return self.reply(404, {"error": "not-found"})
            del folders[item["id"]]
            return self.reply(200, {"deleted": item["id"]})
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        data = json.loads(raw)
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            return self.reply(400, {"error": "invalid-name"})
        if self.command == "POST" and len(parts) == 2:
            identifier = str(self.server.next_id)
            self.server.next_id += 1
            item = {"id": identifier, "name": data["name"]}
            folders[identifier] = item
            return self.reply(201, item)
        if self.command == "PUT" and item:
            item["name"] = data["name"]
            return self.reply(200, item)
        self.reply(404, {"error": "not-found"})

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request


def capture(path, names):
    server = HTTPServer(("127.0.0.1", 0), FolderAPI)
    server.folders = {
        "1": {"id": "1", "name": "Inbox"},
        "2": {"id": "2", "name": "Archive"},
    }
    server.next_id = 3
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = "http://127.0.0.1:" + str(server.server_port)
    entries = []

    def request(method, route, body=None):
        started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        start = time.perf_counter()
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = Request(origin + route, data=data, headers=headers, method=method)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            text = response.read().decode()
            status = response.status
        har_request = {
            "method": method, "url": origin + route, "httpVersion": "HTTP/1.1",
            "headers": [{"name": key, "value": value} for key, value in headers.items()],
            "queryString": [], "cookies": [], "headersSize": -1,
            "bodySize": len(data) if data else 0,
        }
        if data is not None:
            har_request["postData"] = {
                "mimeType": "application/json", "text": data.decode(),
            }
        entries.append({
            "startedDateTime": started,
            "time": (time.perf_counter() - start) * 1000,
            "request": har_request,
            "response": {
                "status": status, "statusText": str(response.reason),
                "httpVersion": "HTTP/1.1", "cookies": [],
                "headers": [{"name": "Content-Type", "value": "application/json"}],
                "content": {"size": len(text.encode()), "mimeType": "application/json", "text": text},
                "redirectURL": "", "headersSize": -1, "bodySize": len(text.encode()),
            },
            "cache": {}, "timings": {"send": 0, "wait": 0, "receive": 0},
        })
        return status, json.loads(text)

    try:
        request("GET", "/api/folders")
        request("GET", "/api/folders/1")
        request("GET", "/api/folders/2")
        for name in names:
            status, created = request("POST", "/api/folders", {"name": name})
            assert status == 201
            route = "/api/folders/" + created["id"]
            request("GET", route)
            request("GET", "/api/folders")
            request("PUT", route, {"name": name + " revised"})
            request("GET", route)
            request("GET", "/api/folders")
            request("DELETE", route)
            status, _ = request("GET", route)
            assert status == 404
            request("GET", "/api/folders")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    path.write_text(json.dumps({
        "log": {"version": "1.2", "creator": {"name": "owned-fixture-recorder", "version": "1"}, "entries": entries},
    }, indent=2) + "\n", encoding="utf-8")
    return len(entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    print("training_exchanges", capture(args.output / "training.har", ("Alpha", "Beta")))
    print("probe_exchanges", capture(args.output / "probe.har", ("Practice", "Second")))
