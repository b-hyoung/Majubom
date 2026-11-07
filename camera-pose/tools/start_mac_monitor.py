"""Run the workspace with a named local USB camera and the Pi stream (Mac/Windows)."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import webbrowser
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from bed_pose.live_sources import named_cameras
from bed_pose.live_web import WebWorkspace, WorkspaceHTTPServer


def select_camera(devices, name):
    matches = [device for device in devices if device['name'].casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValueError(f'USB 카메라 이름이 없거나 중복됩니다: {name}. 장치를 확인하세요.')
    return matches[0]


def reopen_existing(path, installation, port, camera_index, profile, no_browser):
    """Reuse only our authenticated, matching, healthy local workspace."""
    try:
        connection = json.loads(path.read_text())
        origin = f'http://127.0.0.1:{port}'
        if connection['origin'] != origin or connection['installation'] != installation:
            return False
        request = urllib.request.Request(origin+'/api/state',
            headers={'X-BedPose-Token':connection['token']})
        with urllib.request.urlopen(request,timeout=2) as response:
            state = json.load(response)
        sources = {s.get('source_profile',{}).get('camera_index') for s in state['sessions']
                   if s['phase']=='preview' and not s.get('closed')}
        if state['closing'] or not {'raspberry_pi',camera_index} <= sources:
            return False
        if bool(state.get('installation_reference')) != bool(profile):
            return False
        url = origin+'/?view=multi'+('&leg_test=1' if profile else '')+'#'+connection['token']
        if not no_browser:
            webbrowser.open(url)
        print('기존 카메라 작업실을 다시 열었습니다.',flush=True)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera-name', default='USB2.0 PC CAMERA')
    parser.add_argument('--installation', default='mac-pi-20261107')
    parser.add_argument('--port', type=int, default=50966)
    parser.add_argument('--clip-device', choices=('auto', 'mps', 'cpu'), default='auto')
    parser.add_argument('--without-clip', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--test-profile', type=Path, default=None)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', args.installation):
        parser.error('설치 이름은 영문·숫자·밑줄·하이픈으로 지정하세요.')
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    profile_path = args.test_profile or root/'outputs/mac-pi-installation.json'
    profile = json.loads(profile_path.read_text()) if profile_path.is_file() else None
    if profile is not None and profile.get('installation') != args.installation:
        if args.test_profile is not None:
            raise ValueError('테스트 설정의 설치 이름이 현재 설치와 다릅니다.')
        profile = None
    devices = named_cameras()
    camera = select_camera(devices, args.camera_name)
    if (not args.without_clip and args.clip_device=='auto'
            and reopen_existing(root/'outputs/mac-pi-connection.json',args.installation,
                                args.port,camera['index'],profile,args.no_browser)):
        return
    if not (Path.home()/'.config/bed-pose/pi-camera-url.txt').is_file():
        raise ValueError('Pi 연결 설정이 필요합니다. LIVE_MEASUREMENT.md를 확인하세요.')
    workspace = WebWorkspace(output_root=root/'recordings'/args.installation,
                             pose_device='cpu', pose_max_people=1,
                             clip_device=args.clip_device, clip_shared=True)
    server = None
    try:
        if not args.without_clip and not workspace.clip_status['available']:
            raise ValueError(workspace.clip_status['reason'])
        workspace.installation_reference = ({key:profile.get(key) for key in
            ('camera_height_m','bed_surface_height_m','height_source','region_source')}
            if profile is not None else None)
        use_clip = not args.without_clip
        server = WorkspaceHTTPServer(workspace, args.port)
        workspace.scan_state = dict(busy=False, indices=[d['index'] for d in devices],
                                    devices=devices, error=None)
        # A is C920 on Pi; B is the USB camera attached to the analysis computer.
        ids = [workspace.add(dict(kind='pi_camera', use_clip=use_clip))['id'],
               workspace.add(dict(kind='camera', camera=camera['index'],
                                  camera_settings=dict(resolution=[640, 480], fps=30),
                                  use_clip=use_clip))['id']]
        with workspace.lock:
            workspace.room_monitor.configure(dict(workspace.room_monitor.config, bindings=ids,
                installation_confirmed=False, same_person_confirmed=False))
            workspace.room_monitor.set_preview(dict(enabled=False))
        deadline = time.monotonic() + 90
        while True:
            with workspace.lock:
                states = [workspace.sessions[sid].state for sid in ids]
                for state in states:
                    if state.get('phase') == 'error':
                        raise RuntimeError(f"{state['title']}: {state.get('error')}")
                ready = all(state.get('frame_id') is not None for state in states)
            if ready:
                break
            if time.monotonic() > deadline:
                raise RuntimeError('두 카메라의 분석 프레임 준비 시간이 초과됐습니다.')
            time.sleep(.1)
        if profile is not None:
            for sid, entry, selector in zip(ids, profile['regions'], ('Pi C920', args.camera_name)):
                if entry['selector'] != selector:
                    raise ValueError('저장된 침대 영역의 카메라 역할이 다릅니다.')
                state = workspace.sessions[sid].state
                if entry['resolution'] != [state['width'],state['height']]:
                    raise ValueError('저장된 침대 영역과 현재 카메라 해상도가 다릅니다.')
                workspace.command(sid,'region',dict(points=entry['points'],selected_frame_id=state['frame_id']))
            deadline = time.monotonic()+8
            while not all(workspace.sessions[sid].state.get('region') is not None for sid in ids):
                if time.monotonic() > deadline:
                    raise RuntimeError('테스트 침대 영역 적용을 확인하지 못했습니다.')
                time.sleep(.05)
        output = root/'outputs'
        output.mkdir(exist_ok=True)
        connection = output/'mac-pi-connection.json'
        workspace_url = server.origin+'/?view=multi'+('&leg_test=1' if profile else '')+'#'+server.token
        fd = os.open(connection, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(dict(origin=server.origin, token=server.token,
                           url=workspace_url, monitor_url=server.origin+'/monitor#'+server.token,
                           installation=args.installation), stream)
        print('MAC_PI_MONITOR_READY: Pi C920 + ' + camera['name'], flush=True)
        print('보정판 없는 걸터앉음 테스트 준비 완료 · 임시 침대 영역' if profile
              else '새 침대 영역을 등록하세요. 3D는 새 설치 보정 후 사용할 수 있습니다.', flush=True)
        if not args.no_browser:
            webbrowser.open(workspace_url)

        def stop_server():
            workspace.stop_requested.wait()
            server.shutdown()
        threading.Thread(target=stop_server, daemon=True).start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        workspace.close()
        if server is not None:
            server.server_close()


if __name__ == '__main__':
    main()
