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

    debug_html = r"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>FCode Debug Sync</title>
  <style>
    body { margin: 0; display: flex; height: 100vh; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f1115; color: #e2e8f0; overflow: hidden; }
    #left { flex: 7; border-right: 1px solid #334155; position: relative; }
    #right { flex: 3; display: flex; flex-direction: column; background: #1e293b; padding: 20px; box-shadow: inset 5px 0 15px -5px rgba(0,0,0,0.5); }
    iframe { width: 100%; height: 100%; border: none; }
    h2 { margin: 0 0 15px 0; color: #38bdf8; font-size: 1.5rem; text-transform: uppercase; letter-spacing: 1px; }
    .controls { display: flex; flex-direction: column; gap: 10px; margin-bottom: 20px; }
    .control-row { display: flex; align-items: center; gap: 10px; }
    input[type="text"], input[type="number"] { padding: 8px; background: #0f172a; border: 1px solid #475569; color: #fff; border-radius: 6px; outline: none; flex: 1; }
    input[type="text"]:focus, input[type="number"]:focus { border-color: #38bdf8; }
    label { font-size: 0.9rem; color: #cbd5e1; display: flex; align-items: center; gap: 5px; cursor: pointer; }
    #logs-container { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; padding-right: 10px; }
    .log-entry { background: #334155; border-radius: 8px; border-left: 4px solid #4ade80; padding: 12px; font-family: 'Fira Code', 'Courier New', monospace; font-size: 0.85rem; white-space: pre-wrap; word-wrap: break-word; color: #cbd5e1; }
    .log-header { font-weight: bold; color: #94a3b8; margin-bottom: 8px; border-bottom: 1px solid #475569; padding-bottom: 4px; display: flex; justify-content: space-between; }
    .tled { color: #f87171; border-left-color: #f87171; }
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: #1e293b; }
    ::-webkit-scrollbar-thumb { background: #475569; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #64748b; }
  </style>
</head>
<body>
  <div id="left">
    <iframe id="fcode-frame" src="/?replayUrl=/local-replay"></iframe>
  </div>
  <div id="right">
    <h2>Debug Logs</h2>
    <div class="controls">
      <div class="control-row">
        <label>Turn: <input type="number" id="turn-filter" min="0" placeholder="All" oninput="renderLogs()"></label>
        <label><input type="checkbox" id="autosync-toggle" checked onchange="renderLogs()"> Autosync to Replay</label>
      </div>
      <input type="text" id="text-filter" placeholder="Filter by 'core', 'builder', etc..." oninput="renderLogs()">
    </div>
    <div id="logs-container">Loading logs...</div>
  </div>
  <script>
    let logsData = {};
    let lastVisualizerTurn = -1;
    
    async function loadLogs() {
      try {
        const res = await fetch('/logs.json');
        logsData = await res.json();
        renderLogs();
        setInterval(syncWithIframe, 100);
      } catch (e) {
        document.getElementById('logs-container').innerHTML = 'Failed to load logs.';
      }
    }
    
    function syncWithIframe() {
        if (!document.getElementById('autosync-toggle').checked) return;
        try {
            const iframe = document.getElementById('fcode-frame');
            if (!iframe.contentWindow || !iframe.contentWindow.document) return;
            const doc = iframe.contentWindow.document;
            const allElements = doc.querySelectorAll('*');
            let foundTurn = -1;
            for (let el of allElements) {
                if (el.children.length === 0 && el.textContent) {
                    const match = el.textContent.trim().match(/^(?:Turn|Round)[:\s]*(\d+)$/i);
                    if (match) {
                        foundTurn = parseInt(match[1]);
                        break;
                    }
                }
            }
            if (foundTurn !== -1 && foundTurn !== lastVisualizerTurn) {
                lastVisualizerTurn = foundTurn;
                document.getElementById('turn-filter').value = foundTurn;
                renderLogs();
            }
        } catch (e) {}
    }
    
    function renderLogs() {
      const container = document.getElementById('logs-container');
      container.innerHTML = '';
      
      const textFilter = document.getElementById('text-filter').value.toLowerCase();
      const turnFilterRaw = document.getElementById('turn-filter').value;
      const turnFilter = turnFilterRaw === '' ? null : parseInt(turnFilterRaw);
      
      let hasLogs = false;
      const turns = Object.keys(logsData).map(Number).sort((a,b) => a - b);
      
      for (const turn of turns) {
        if (turnFilter !== null && turn !== turnFilter) continue;
        
        const turnLogs = logsData[turn];
        for (const log of turnLogs) {
            const searchString = `turn ${turn} entity ${log.id} ${log.text}`.toLowerCase();
            if (textFilter && !searchString.includes(textFilter)) continue;
            
            hasLogs = true;
            const el = document.createElement('div');
            el.className = 'log-entry' + (log.tled ? ' tled' : '');
            
            const header = document.createElement('div');
            header.className = 'log-header';
            header.innerHTML = `<span>Turn ${turn} | Entity ${log.id}</span><span>${log.time/1000}ms${log.tled ? ' (TLED)' : ''}</span>`;
            
            const body = document.createElement('div');
            body.textContent = log.text;
            
            el.appendChild(header);
            el.appendChild(body);
            container.appendChild(el);
        }
      }
      
      if (!hasLogs) {
        container.innerHTML = '<div style="color: #64748b; text-align: center; margin-top: 20px;">No logs match your filters.</div>';
      }
    }
    
    loadLogs();
  </script>
</body>
</html>
""".encode("utf-8")

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
