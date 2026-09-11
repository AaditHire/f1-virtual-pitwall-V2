"""Local-only review UI for Phase 12C external human transcript candidates."""
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

from scripts.phase12c_benchmark import FrozenManifest, atomic_write
from scripts.phase12c_recovery import (
    RecoveryReviewDataset,
    load_external_references,
    save_review,
    validate_against_manifest,
)

MAX_AUDIO_BYTES = 5_000_000
HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Phase 12C External Reference Review</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#090d10;color:#f5f7f8}*{box-sizing:border-box}body{margin:0}.bar{position:sticky;top:0;z-index:2;border-bottom:1px solid #344049;background:#090d10;padding:15px 22px;display:flex;gap:16px;justify-content:space-between}.eyebrow{font:650 11px ui-monospace;letter-spacing:.13em;color:#a9b6bf;text-transform:uppercase}main{max-width:980px;margin:auto;padding:26px 20px 60px}h1{margin:5px 0 4px;font-size:28px}.meta{display:flex;gap:10px;flex-wrap:wrap;color:#bac5cb;font:13px ui-monospace}.tag{border:1px solid #59656d;padding:5px 8px}.likely{border-color:#e7a928;color:#ffd06a}.none{border-color:#65727a;color:#b9c3c9}.critical{border-color:#ef3340;color:#ff7b84}.panel{border:1px solid #344049;background:#0d1216;padding:18px;margin-top:16px}.warning{border-left:3px solid #ef3340;padding:10px 13px;background:#151a1e;margin:16px 0}audio{width:100%;margin:10px 0}label{display:block;color:#c5cfd5;font-size:13px;margin:12px 0 6px}textarea,select,input{width:100%;background:#080c0f;color:#fff;border:1px solid #46545d;padding:10px;font:14px ui-monospace}textarea{min-height:110px}.source{font:18px ui-monospace;line-height:1.5;padding:12px;border-left:3px solid #e7a928;background:#11171b}.reason{line-height:1.5;color:#c6d0d6}.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}.term{display:grid;grid-template-columns:220px 1fr auto;gap:8px;margin:8px 0}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:16px}button,a.button{border:1px solid #53636d;background:#182127;color:#fff;padding:10px 13px;font-weight:650;text-decoration:none;cursor:pointer}button.primary{background:#ef3340;border-color:#ef3340}button:disabled{opacity:.35;cursor:not-allowed}.ok{color:#55d98a}.error{color:#ff737c;white-space:pre-wrap}small{color:#9eabb4}button:focus,a:focus,input:focus,textarea:focus,select:focus{outline:2px solid #fff;outline-offset:2px}@media(max-width:650px){.row,.term{grid-template-columns:1fr}.bar{position:static;flex-direction:column}}
</style></head><body><header class="bar"><div><div class="eyebrow">Human-source review · ASR isolated</div><strong>VPW Phase 12C Recovery</strong></div><div id="progress">Loading…</div></header><main><div class="eyebrow" id="position"></div><h1 id="clipId">Clip</h1><div class="meta"><span class="tag" id="statusTag"></span><span class="tag" id="criticalTag"></span><span id="metadata"></span></div><div class="warning"><strong>No machine transcript is loaded.</strong> Review only the original audio, independent metadata, and the cited human-editorial candidate. If identity or completeness is uncertain, require manual transcription.</div>
<section class="panel"><div class="eyebrow">Original frozen audio</div><audio id="audio" controls preload="none"></audio><button id="replay">Replay from start</button></section>
<section class="panel"><div class="eyebrow">External human-written candidate</div><p class="source" id="candidate"></p><p class="reason" id="reason"></p><a class="button" id="sourceLink" target="_blank" rel="noopener noreferrer">Open source</a></section>
<section class="panel"><label for="accepted">Accepted literal reference (review/edit only after listening)</label><textarea id="accepted" spellcheck="false"></textarea><div class="row"><div><label for="usability">Audio usability</label><select id="usability"><option value="">Select…</option><option>CLEAR</option><option>PARTIAL</option><option>POOR</option><option>UNUSABLE</option></select></div><div><label for="speaker">Speaker type</label><select id="speaker"><option value="">Select…</option><option>DRIVER</option><option>ENGINEER</option><option>MIXED</option><option>UNKNOWN</option></select></div></div><label>Critical terms in the accepted human transcript</label><small>Required for strategy-critical acceptance. Add only exact phrases after listening.</small><div id="terms"></div><button id="addTerm">Add critical term</button><label for="notes">Review notes</label><textarea id="notes" style="min-height:70px"></textarea><div class="actions"><button id="previous">Previous</button><button id="accept" class="primary">Accept reference</button><button id="reject">Reject</button><button id="likely">Downgrade to likely</button><button id="noReference">Mark no reference</button><button id="manual">Needs manual transcription</button><button id="next">Next</button></div><p id="saveStatus"></p></section></main><script>
const categories=['DRIVER_NAME','CAR_NUMBER','LAP_NUMBER','TYRE_COMPOUND','BOX_PIT','STAY_OUT','DRS','SAFETY_CAR','VSC','DELTA','BRAKE_BALANCE','DIFFERENTIAL','ENGINE_STRAT_MODE','WING_ADJUSTMENT','OTHER_OPERATIONAL'];let index=[],cursor=0,current=null;const $=id=>document.getElementById(id);function termRow(term={category:'OTHER_OPERATIONAL',text:''}){const row=document.createElement('div');row.className='term';const select=document.createElement('select');categories.forEach(category=>{const option=document.createElement('option');option.textContent=category;option.value=category;option.selected=category===term.category;select.appendChild(option)});const input=document.createElement('input');input.value=term.text;input.placeholder='Exact phrase from accepted reference';const remove=document.createElement('button');remove.textContent='Remove';remove.onclick=()=>row.remove();row.append(select,input,remove);$('terms').appendChild(row)}function terms(){return [...document.querySelectorAll('.term')].map(row=>({category:row.children[0].value,text:row.children[1].value.trim()})).filter(term=>term.text)}async function loadIndex(){index=await(await fetch('/api/index')).json();cursor=Math.max(0,index.findIndex(x=>!x.reviewed));await loadClip()}async function loadClip(){current=await(await fetch('/api/clip/'+index[cursor].clip_id)).json();$('position').textContent=`Clip ${current.sequence} of ${current.total}`;$('clipId').textContent=current.clip_id;$('metadata').textContent=`${current.event} · ${current.driver_display_name} · lap ${current.causal_leader_lap??'pre-cutoff'} · ${current.recording_timestamp}`;$('statusTag').textContent=current.verification_status;$('statusTag').className='tag '+(current.verification_status==='VERIFIED_LIKELY'?'likely':'none');$('criticalTag').textContent=current.strategy_critical?'STRATEGY-CRITICAL · HUMAN REVIEW':'NON-CRITICAL';$('criticalTag').className='tag '+(current.strategy_critical?'critical':'');$('audio').src=current.audio_path;$('candidate').textContent=current.recovered_transcript||'No trustworthy external literal transcript recovered.';$('reason').textContent=current.verification_reason;$('sourceLink').href=current.source_url||'#';$('sourceLink').hidden=!current.source_url;const d=current.decision||{};$('accepted').value=d.accepted_transcript||current.recovered_transcript||'';$('usability').value=d.usability||'';$('speaker').value=d.speaker_type||'';$('notes').value=d.notes||'';$('terms').replaceChildren();(d.critical_terms||[]).forEach(termRow);$('accept').disabled=!current.recovered_transcript;$('saveStatus').textContent=d.action?`Saved: ${d.action}`:'';$('progress').textContent=`${index.filter(x=>x.reviewed).length} / ${index.length} reviewed`}
async function act(action){const body={action,accepted_transcript:$('accepted').value||null,usability:$('usability').value||null,speaker_type:$('speaker').value||null,critical_terms:terms(),notes:$('notes').value};const r=await fetch('/api/clip/'+current.clip_id,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok){$('saveStatus').textContent=data.detail||'Save failed';$('saveStatus').className='error';return}index[cursor].reviewed=true;$('saveStatus').textContent='Saved: '+data.action;$('saveStatus').className='ok';$('progress').textContent=`${index.filter(x=>x.reviewed).length} / ${index.length} reviewed`}
$('replay').onclick=()=>{$('audio').currentTime=0;$('audio').play()};$('addTerm').onclick=()=>termRow();$('accept').onclick=()=>act('ACCEPT_REFERENCE');$('reject').onclick=()=>act('REJECT_REFERENCE');$('likely').onclick=()=>act('DOWNGRADE_TO_LIKELY');$('noReference').onclick=()=>act('MARK_NO_REFERENCE');$('manual').onclick=()=>act('NEEDS_MANUAL_TRANSCRIPTION');$('previous').onclick=async()=>{cursor=Math.max(0,cursor-1);await loadClip()};$('next').onclick=async()=>{cursor=Math.min(index.length-1,cursor+1);await loadClip()};loadIndex().catch(e=>{$('saveStatus').textContent=e;$('saveStatus').className='error'});
</script></body></html>"""


class RecoveryReviewApplication:
    def __init__(self, manifest_path: Path, external_path: Path, reviews_path: Path):
        self.manifest_path = manifest_path
        self.manifest = FrozenManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        self.external = load_external_references(external_path)
        validate_against_manifest(self.external, self.manifest, manifest_path)
        self.reviews_path = reviews_path
        self.reviews = RecoveryReviewDataset.model_validate_json(
            reviews_path.read_text(encoding="utf-8")
        )
        if self.reviews.selection_hash != self.manifest.selection_hash:
            raise ValueError("review file does not match frozen selection")
        self.clips = {clip.clip_id: clip for clip in self.manifest.clips}
        self.records = {record.clip_id: record for record in self.external.records}
        self.lock = threading.Lock()

    def ordered_records(self):
        priority = {
            "VERIFIED_LIKELY": 0,
            "VERIFIED_EXACT": 1,
            "REFERENCE_CONFLICT": 2,
            "NO_REFERENCE": 3,
        }
        return sorted(
            self.external.records,
            key=lambda record: (priority[record.verification_status], record.clip_id),
        )

    def index(self):
        return [
            {"clip_id": record.clip_id, "reviewed": record.clip_id in self.reviews.decisions}
            for record in self.ordered_records()
        ]

    def clip(self, clip_id: str):
        record = self.records[clip_id]
        order = self.ordered_records()
        decision = self.reviews.decisions.get(clip_id)
        return {
            "clip_id": clip_id,
            "sequence": next(
                index for index, item in enumerate(order, 1) if item.clip_id == clip_id
            ),
            "total": len(order),
            "event": record.event,
            "driver_display_name": record.driver_display_name,
            "causal_leader_lap": record.causal_leader_lap,
            "recording_timestamp": record.recording_timestamp,
            "verification_status": record.verification_status,
            "strategy_critical": record.strategy_critical,
            "recovered_transcript": record.recovered_transcript,
            "verification_reason": record.verification_reason,
            "source_name": record.source_name,
            "source_url": record.source_url,
            "audio_path": f"/audio/{clip_id}",
            "decision": decision.model_dump(mode="json") if decision else None,
        }

    def save(self, clip_id: str, payload: dict):
        with self.lock:
            decision = save_review(self.reviews, self.external, clip_id, payload)
            atomic_write(self.reviews_path, self.reviews)
            return decision.model_dump(mode="json")

    def audio(self, clip_id: str):
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            response = client.get(self.clips[clip_id].audio_url)
            response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if not content_type.startswith("audio/") or not response.content:
            raise ValueError("provider did not return audio")
        if len(response.content) > MAX_AUDIO_BYTES:
            raise ValueError("audio exceeds bounded proxy limit")
        return content_type, response.content


def handler_for(application: RecoveryReviewApplication):
    class Handler(BaseHTTPRequestHandler):
        def send_value(self, status, value, content_type="application/json; charset=utf-8"):
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
                result = application.save(
                    path.rsplit("/", 1)[-1], json.loads(self.rfile.read(length))
                )
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
        "--external", type=Path, default=Path("docs/phase12c-radio-external-references.json")
    )
    parser.add_argument(
        "--reviews", type=Path, default=Path("docs/phase12c-radio-recovery-reviews.json")
    )
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-open", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    application = RecoveryReviewApplication(args.manifest, args.external, args.reviews)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(application))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Phase 12C external-reference review: {url}")
    print("ASR predictions are not loaded. Press Ctrl+C to stop.")
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
