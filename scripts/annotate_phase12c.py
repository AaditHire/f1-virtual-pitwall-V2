"""Blind, local-only human reference annotation tool for Phase 12C."""
# ruff: noqa: E501

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from pydantic import ValidationError

from scripts.phase12c_benchmark import (
    FrozenManifest,
    ReferenceDataset,
    annotation_payload,
    atomic_write,
    save_reference,
)

MAX_AUDIO_BYTES = 5_000_000
HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Phase 12C Blind Radio Annotation</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#090d10;color:#f5f7f8}
*{box-sizing:border-box}body{margin:0}.bar{border-bottom:1px solid #344049;padding:16px 24px;display:flex;gap:16px;align-items:center;justify-content:space-between}
main{max-width:900px;margin:auto;padding:28px 20px 60px}.eyebrow{font:600 12px ui-monospace;letter-spacing:.12em;color:#a9b6bf;text-transform:uppercase}
h1{margin:5px 0 8px;font-size:28px}.notice{border-left:3px solid #ef3340;background:#141a1e;padding:12px 14px;margin:18px 0;color:#d8e0e5}
.panel{border:1px solid #344049;background:#0d1216;padding:18px;margin-top:16px}.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
label{display:block;font-size:13px;color:#c4cfd6;margin:12px 0 6px}textarea,select,input{width:100%;background:#080c0f;color:#fff;border:1px solid #46545d;padding:10px;font:15px ui-monospace}
textarea{min-height:150px;resize:vertical}audio{width:100%;margin:12px 0}.actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
button{border:1px solid #53636d;background:#182127;color:#fff;padding:10px 14px;font-weight:650;cursor:pointer}button.primary{background:#ef3340;border-color:#ef3340}
button:focus,input:focus,textarea:focus,select:focus{outline:2px solid #fff;outline-offset:2px}.term{display:grid;grid-template-columns:220px 1fr auto;gap:8px;margin:8px 0}
.complete{color:#55d98a}.draft{color:#ffbf47}.error{color:#ff737c;white-space:pre-wrap}small{color:#9eabb4}@media(max-width:650px){.row,.term{grid-template-columns:1fr}.bar{align-items:flex-start;flex-direction:column}}
</style></head><body>
<header class="bar"><div><div class="eyebrow">Human reference benchmark · blind mode</div><strong>VPW Phase 12C</strong></div><div id="progress">Loading…</div></header>
<main><div class="eyebrow" id="position"></div><h1 id="clipId">Clip</h1>
<div class="notice"><strong>Listen without race context.</strong> ASR predictions, confidence and disagreement are not loaded by this tool. Type only what you genuinely hear; use <code>[inaudible]</code> instead of guessing.</div>
<section class="panel"><div class="eyebrow">Source audio</div><audio id="audio" controls preload="none"></audio><button id="replay">Replay from start</button></section>
<section class="panel"><label for="transcript">Literal human reference transcript</label><textarea id="transcript" spellcheck="false" placeholder="Transcribe spoken content exactly. Do not expand or correct meaning."></textarea>
<button id="inaudible">Insert [inaudible]</button><div class="row"><div><label for="usability">Audio usability</label><select id="usability"><option value="">Select…</option><option>CLEAR</option><option>PARTIAL</option><option>POOR</option><option>UNUSABLE</option></select></div>
<div><label for="speaker">Speaker type</label><select id="speaker"><option value="">Select…</option><option>DRIVER</option><option>ENGINEER</option><option>MIXED</option><option>UNKNOWN</option></select></div></div>
<label>Critical motorsport terms heard in the reference</label><small>Add these only after writing the literal reference. The exact text must occur in that reference.</small><div id="terms"></div><button id="addTerm">Add critical term</button>
<label for="notes">Annotation notes</label><textarea id="notes" style="min-height:80px" placeholder="Optional uncertainty or audio-quality notes. Do not use ASR output."></textarea>
<div class="actions"><button id="previous">Previous</button><button id="draft">Save draft</button><button id="complete" class="primary">Mark complete & save</button><button id="next">Next</button></div><p id="status"></p></section></main>
<script>
const categories=['DRIVER_NAME','CAR_NUMBER','LAP_NUMBER','TYRE_COMPOUND','BOX_PIT','STAY_OUT','DRS','SAFETY_CAR','VSC','DELTA','BRAKE_BALANCE','DIFFERENTIAL','ENGINE_STRAT_MODE','WING_ADJUSTMENT','OTHER_OPERATIONAL'];
let index=[],cursor=0,current=null;
const $=id=>document.getElementById(id);
function termRow(term={category:'OTHER_OPERATIONAL',text:''}){const row=document.createElement('div');row.className='term';const select=document.createElement('select');categories.forEach(c=>{const o=document.createElement('option');o.textContent=c;o.value=c;o.selected=c===term.category;select.appendChild(o)});const input=document.createElement('input');input.value=term.text;input.placeholder='Exact phrase from reference';const remove=document.createElement('button');remove.textContent='Remove';remove.onclick=()=>row.remove();row.append(select,input,remove);$('terms').appendChild(row)}
function terms(){return [...document.querySelectorAll('.term')].map(r=>({category:r.children[0].value,text:r.children[1].value.trim()})).filter(t=>t.text)}
async function loadIndex(){const r=await fetch('/api/index');index=await r.json();cursor=Math.max(0,index.findIndex(x=>!x.complete));await loadClip()}
async function loadClip(){current=index[cursor];const r=await fetch('/api/clip/'+current.clip_id);const data=await r.json();$('position').textContent=`Clip ${data.sequence} of ${data.total}`;$('clipId').textContent=data.clip_id;$('audio').src=data.audio_path;const a=data.annotation||{};$('transcript').value=a.reference_transcript||'';$('usability').value=a.usability||'';$('speaker').value=a.speaker_type||'';$('notes').value=a.notes||'';$('terms').replaceChildren();(a.critical_terms||[]).forEach(termRow);$('status').textContent=a.complete?'Saved complete':a.updated_at?'Draft saved':'';$('status').className=a.complete?'complete':'draft';$('progress').textContent=`${index.filter(x=>x.complete).length} / ${index.length} complete`}
async function save(complete){$('status').textContent='Saving…';$('status').className='';const body={reference_transcript:$('transcript').value,usability:$('usability').value||null,speaker_type:$('speaker').value||null,critical_terms:terms(),notes:$('notes').value,complete};const r=await fetch('/api/clip/'+current.clip_id,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok){$('status').textContent=data.detail||'Save failed';$('status').className='error';return false}current.complete=data.complete;$('status').textContent=data.complete?'Saved complete':'Draft saved';$('status').className=data.complete?'complete':'draft';$('progress').textContent=`${index.filter(x=>x.complete).length} / ${index.length} complete`;return true}
$('replay').onclick=()=>{$('audio').currentTime=0;$('audio').play()};$('inaudible').onclick=()=>{const t=$('transcript'),p=t.selectionStart;t.setRangeText('[inaudible]',p,t.selectionEnd,'end');t.focus()};$('addTerm').onclick=()=>termRow();$('draft').onclick=()=>save(false);$('complete').onclick=()=>save(true);$('previous').onclick=async()=>{cursor=Math.max(0,cursor-1);await loadClip()};$('next').onclick=async()=>{cursor=Math.min(index.length-1,cursor+1);await loadClip()};loadIndex().catch(e=>{$('status').textContent=e;$('status').className='error'});
</script></body></html>"""


class AnnotationApplication:
    def __init__(self, manifest_path: Path, references_path: Path):
        self.manifest_path = manifest_path
        self.references_path = references_path
        self.manifest = FrozenManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        self.references = ReferenceDataset.model_validate_json(
            references_path.read_text(encoding="utf-8")
        )
        if self.references.selection_hash != self.manifest.selection_hash:
            raise ValueError("reference file does not match the frozen selection")
        self.clips = {clip.clip_id: clip for clip in self.manifest.clips}
        self.lock = threading.Lock()

    def index(self) -> list[dict]:
        return [
            {
                "clip_id": clip.clip_id,
                "sequence": clip.sequence,
                "complete": bool(
                    self.references.annotations.get(clip.clip_id)
                    and self.references.annotations[clip.clip_id].complete
                ),
            }
            for clip in self.manifest.clips
        ]

    def clip(self, clip_id: str) -> dict:
        return annotation_payload(self.manifest, self.references, clip_id)

    def save(self, clip_id: str, payload: dict) -> dict:
        with self.lock:
            result = save_reference(self.references, self.manifest, clip_id, payload)
            atomic_write(self.references_path, self.references)
            return result.model_dump(mode="json")

    def audio(self, clip_id: str) -> tuple[str, bytes]:
        clip = self.clips[clip_id]
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            response = client.get(clip.audio_url)
            response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if not content_type.startswith("audio/") or not response.content:
            raise ValueError("provider did not return audio")
        if len(response.content) > MAX_AUDIO_BYTES:
            raise ValueError("audio exceeds bounded proxy limit")
        return content_type, response.content


def handler_for(application: AnnotationApplication):
    class Handler(BaseHTTPRequestHandler):
        def send_value(self, status: int, value, content_type="application/json; charset=utf-8"):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            try:
                if path == "/":
                    return self.send_value(200, HTML.encode(), "text/html; charset=utf-8")
                if path == "/api/index":
                    return self.send_value(200, application.index())
                if path.startswith("/api/clip/"):
                    return self.send_value(200, application.clip(path.rsplit("/", 1)[-1]))
                if path.startswith("/audio/"):
                    content_type, data = application.audio(path.rsplit("/", 1)[-1])
                    return self.send_value(200, data, content_type)
                self.send_value(404, {"detail": "not found"})
            except KeyError:
                self.send_value(404, {"detail": "unknown clip"})
            except (httpx.HTTPError, ValueError) as exc:
                self.send_value(502, {"detail": f"audio unavailable: {type(exc).__name__}"})

        def do_POST(self):
            path = unquote(urlparse(self.path).path)
            if not path.startswith("/api/clip/"):
                return self.send_value(404, {"detail": "not found"})
            try:
                length = int(self.headers.get("content-length", "0"))
                if length <= 0 or length > 20_000:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length))
                result = application.save(path.rsplit("/", 1)[-1], payload)
                self.send_value(200, result)
            except KeyError:
                self.send_value(404, {"detail": "unknown clip"})
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                self.send_value(422, {"detail": str(exc)})

        def log_message(self, pattern, *args):
            return

    return Handler


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, default=Path("docs/phase12c-radio-annotation-manifest.json")
    )
    parser.add_argument(
        "--references", type=Path, default=Path("docs/phase12c-radio-human-references.json")
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    application = AnnotationApplication(args.manifest, args.references)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(application))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Blind Phase 12C annotation tool: {url}")
    print("Press Ctrl+C to stop. Progress is saved after every draft/complete action.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
