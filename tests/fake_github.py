"""A tiny fake of GitHub's contents API (one repo, one file) for testing the website's sharing offline.

    python tests/fake_github.py 8822 /tmp/state-dir
Then in the browser: localStorage.setItem("cfbPicks.apiBase", "http://127.0.0.1:8822")."""
import base64
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIR = sys.argv[2] if len(sys.argv) > 2 else "."
FILE = os.path.join(DIR, "state.json")


def sha(b):
    return hashlib.sha1(b).hexdigest()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-GitHub-Api-Version, Accept")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")

    def reply(self, code, obj=None):
        b = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.reply(204)

    def authed(self):
        if self.headers.get("Authorization") != "Bearer good-token":
            self.reply(401, {"message": "Bad credentials"})
            return False
        return True

    def do_GET(self):
        if not self.authed():
            return
        if self.path.endswith("/contents/state.json"):
            if not os.path.exists(FILE):
                return self.reply(404, {"message": "Not Found"})
            b = open(FILE, "rb").read()
            return self.reply(200, {"sha": sha(b), "size": len(b), "content": base64.b64encode(b).decode()})
        return self.reply(200, {"name": "cfb-picks-data", "private": True})

    def do_PUT(self):
        if not self.authed():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cur = open(FILE, "rb").read() if os.path.exists(FILE) else None
        if cur is not None and body.get("sha") != sha(cur):
            return self.reply(409, {"message": "sha mismatch"})
        if cur is None and body.get("sha"):
            return self.reply(422, {"message": "file missing"})
        new = base64.b64decode(body["content"])
        open(FILE, "wb").write(new)
        return self.reply(201 if cur is None else 200, {"content": {"sha": sha(new)}})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
