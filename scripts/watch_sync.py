#!/usr/bin/env python3
import sys
import json
import http.server
import threading
import webbrowser
import mimetypes
from pathlib import Path

# Add fcode to path to find dist
try:
    from fcode.commands.watch import _find_dist
except ImportError:
    print("Could not import fcode. Are you running in the venv?")
    sys.exit(1)

# Import florent_replay_pb2 from scripts dir
sys.path.append(str(Path(__file__).parent.resolve()))
import florent_replay_pb2

def extract_logs(replay_path):
    with open(replay_path, "rb") as f:
        data = f.read()
    r = florent_replay_pb2.Replay()
    r.ParseFromString(data)
    
    logs_by_turn = {}
    for i, turn in enumerate(r.turns):
        turn_logs = []
        for update in turn.updates:
            kind = update.WhichOneof("kind")
            if kind == "botOutput":
                payload = getattr(update, kind)
                if payload.stdout:
                    turn_logs.append({
                        "id": payload.id,
                        "text": payload.stdout.strip(),
                        "time": payload.execTimeUs,
                        "tled": payload.tled
                    })
        if turn_logs:
            logs_by_turn[i] = turn_logs
            
    return logs_by_turn

def main():
    if len(sys.argv) < 2:
        print("Usage: python watch_sync.py <path_to_replay.replay26>")
        sys.exit(1)

    replay_path = Path(sys.argv[1]).resolve()
    if not replay_path.exists():
        print(f"File not found: {replay_path}")
        sys.exit(1)

    print(f"Extracting logs from {replay_path.name}...")
    logs = extract_logs(replay_path)
    logs_json = json.dumps(logs).encode("utf-8")
    
    dist_path = _find_dist()
    if not dist_path:
        print("Visualiser not found. Try reinstalling fcode.")
        sys.exit(1)

    viewer_path = Path(__file__).parent / "debug_viewer.html"
    if not viewer_path.exists():
        print(f"Error: {viewer_path} not found.")
        sys.exit(1)
        
    debug_html = viewer_path.read_bytes()

    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")
    mimetypes.add_type("application/wasm", ".wasm")
    mimetypes.add_type("text/css", ".css")

    class DualHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=dist_path, **kwargs)

        def do_GET(self):
            if self.path == "/local-replay":
                try:
                    data = replay_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:
                    self.send_error(500, "Failed to read replay file")
                return
            elif self.path == "/debug.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(debug_html)))
                self.end_headers()
                self.wfile.write(debug_html)
                return
            elif self.path == "/logs.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(logs_json)))
                self.end_headers()
                self.wfile.write(logs_json)
                return
            super().do_GET()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), DualHandler)
    port = server.server_address[1]

    url = f"http://127.0.0.1:{port}/debug.html"
    print(f"Serving debug viewer at {url}")
    print(f"Replay: {replay_path.name}")
    print("Press Ctrl+C to stop")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()

if __name__ == "__main__":
    main()
