"""Local two-camera GPU monitor with CLIP and restored physical camera roles."""
import json,time,threading,os
from pathlib import Path
from bed_pose.live_web import WebWorkspace,WorkspaceHTTPServer
from bed_pose.live_sources import named_cameras


def main():
    directory=Path('outputs');directory.mkdir(exist_ok=True)
    workspace=WebWorkspace(output_root=Path('recordings/live'),pose_device='browser_gpu',pose_max_people=1,
        clip_device='cuda',clip_precision='amp-fp16',clip_shared=True,clip_batch_size=2,clip_batch_wait_ms=2)
    server=None
    crosscheck=None
    try:
        tof_url=os.environ.get('BEDPOSE_TOF_URL')
        if tof_url:
            from bed_pose.sensor_crosscheck import SensorCrosscheck
            crosscheck=SensorCrosscheck(tof_url);workspace.room_monitor.sensor_crosscheck=crosscheck
        if not workspace.clip_status['available']:
            raise RuntimeError('CLIP unavailable; run training/Setup-Training.ps1 and python -m bed_pose clip-setup')
        server=WorkspaceHTTPServer(workspace,50966)
        connection=directory/'live-connection.json'
        if connection.is_file():server.token=json.loads(connection.read_text(encoding='utf-8'))['token']
        devices=named_cameras();workspace.scan_state=dict(busy=False,indices=[d['index'] for d in devices],devices=devices,error=None)
        ids=[]
        for selector in ('C920','USB2.0 PC CAMERA'):
            matches=[d for d in devices if selector.casefold() in d['name'].casefold()]
            if len(matches)>1:raise RuntimeError('Ambiguous physical camera: '+selector)
            ids.append(workspace.add(dict(kind='camera',camera=matches[0]['index'],
                camera_settings=dict(resolution=[640,480],fps=30),use_clip=True))['id'] if matches else '')
            if ids[-1]:workspace.sessions[ids[-1]].worker.engine.body_state_provider.input_preview_enabled=False
        with workspace.lock:
            workspace.room_monitor.configure(dict(workspace.room_monitor.config,bindings=ids))
            workspace.room_monitor.set_preview({'enabled':True})
            if workspace.room_monitor.preview_bed is not None:workspace.room_monitor.align_preview({'reference':'bed'})
        # Reuse the user's existing 2D corners in the independent CLIP/bed path.
        deadline=time.monotonic()+25;restored=set()
        while time.monotonic()<deadline and len(restored)<sum(bool(x) for x in ids):
            with workspace.lock:
                annotation=workspace.room_monitor.preview_annotation
                for i,sid in enumerate(ids):
                    if not sid or sid in restored:continue
                    state=workspace.sessions[sid].state
                    if state.get('phase')=='error':raise RuntimeError('Camera failed: '+str(state.get('error')))
                    if state.get('frame_id') is None:continue
                    if annotation and annotation['resolutions'][i]==[state['width'],state['height']]:
                        workspace.command(sid,'region',dict(points=annotation['points'][i],selected_frame_id=state['frame_id']))
                    restored.add(sid)
            time.sleep(.05)
        connection.write_text(json.dumps(dict(origin=server.origin,token=server.token,url=server.origin+'/monitor#'+server.token)),encoding='utf-8')
        print('GPU_MONITOR_READY',flush=True)
        def shutdown_when_requested():
            workspace.stop_requested.wait();server.shutdown()
        threading.Thread(target=shutdown_when_requested,daemon=True).start()
        server.serve_forever()
    finally:
        if crosscheck is not None:crosscheck.close()
        workspace.close()
        if server is not None:server.server_close()


if __name__=='__main__':main()
