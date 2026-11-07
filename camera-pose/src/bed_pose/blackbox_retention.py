"""Conservative cleanup for completed blackbox segments and paired groups.

The caller must hold the workspace and record-library locks through a cleanup.
Unknown files, incomplete segments, active or pinned records are kept.
"""
import os
from pathlib import Path
import re
import shutil
import uuid

from .blackbox_storage import blackbox_root, validate_segment
from .io_utils import load_json, atomic_json
from .live_recording import SUPPORTED_SCHEMAS

ID=re.compile(r'[0-9a-f]{32}')
FILES={'manifest.json','live_frames.jsonl','control_events.jsonl','analysis_video.mkv','video_frames.jsonl'}
PIN='.keep'


def _size(root):
    """Count all regular files, never following links; refuse uncertain totals."""
    total=0
    if not root.exists():return 0
    stack=[root]
    while stack:
        directory=stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():raise ValueError('블랙박스 저장소에 심볼릭 링크가 있습니다.')
                if entry.is_dir(follow_symlinks=False):stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):total+=entry.stat(follow_symlinks=False).st_size
                else:raise ValueError('블랙박스 저장소에 지원하지 않는 파일이 있습니다.')
    return total


def _group_members(root):
    groups=root/'groups';protected=set()
    if not groups.exists():return protected
    if groups.is_symlink() or not groups.is_dir():raise ValueError('그룹 기록을 확인할 수 없습니다.')
    for directory in groups.iterdir():
        if directory.is_symlink() or not directory.is_dir() or not ID.fullmatch(directory.name):
            raise ValueError('그룹 기록을 확인할 수 없습니다.')
        manifest=directory/'manifest.json'
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size>4*1024**2:
            raise ValueError('그룹 기록을 확인할 수 없습니다.')
        data=load_json(manifest)
        if not isinstance(data,dict) or data.get('group_id')!=directory.name or not isinstance(data.get('members'),list):
            raise ValueError('그룹 기록을 확인할 수 없습니다.')
        if data.get('phase')=='completed' and data.get('segment_seconds') is not None:
            continue  # Group members remain protected individually by their group_id marker.
        for member in data['members']:
            if not isinstance(member,dict):raise ValueError('그룹 기록 연결을 확인할 수 없습니다.')
            path=member.get('run_directory')
            if path is None:continue
            if not isinstance(path,str):raise ValueError('그룹 기록 연결을 확인할 수 없습니다.')
            ref=Path(path)
            if ref.parent!=root or not ID.fullmatch(ref.name):raise ValueError('그룹 기록 연결을 확인할 수 없습니다.')
            protected.add(ref.name)
    return protected


def _candidate(directory,protected,group_id=None,segment_index=None):
    if directory.name in protected or directory.is_symlink() or not directory.is_dir() or not ID.fullmatch(directory.name):return None
    files=list(directory.iterdir())
    if any(p.name==PIN for p in files):return None
    if any(p.name not in FILES or p.is_symlink() or not p.is_file() for p in files):return None
    manifest=directory/'manifest.json'
    if not manifest.is_file() or manifest.stat().st_size>4*1024**2:return None
    try:
        data=load_json(manifest)
        validate_segment(data['blackbox_segment'])
        segment=data['blackbox_segment']
        if segment.get('group_id')!=group_id or (segment_index is not None and segment['index']!=segment_index):return None
        timestamp=data['started_utc_ns']
        if (data.get('storage_class')!='blackbox' or data.get('measurement_id')!=directory.name
                or data.get('schema_version') not in SUPPORTED_SCHEMAS or data.get('ledger_complete') is not True
                or data.get('run_status') not in ('succeeded','failed')
                or not isinstance(timestamp,str) or not timestamp.isascii() or not timestamp.isdigit()
                or len(timestamp)>24 or not {'manifest.json','live_frames.jsonl','control_events.jsonl'} <= {p.name for p in files}):
            return None
        names={p.name for p in files}
        if type(data.get('video_recording')) is not bool:return None
        if data['video_recording']:
            video=data.get('video_artifact')
            if (not isinstance(video,dict) or video.get('version')!='analysis-video-0.1'
                    or video.get('index_path')!='video_frames.jsonl'
                    or names!={'manifest.json','live_frames.jsonl','control_events.jsonl','video_frames.jsonl','analysis_video.mkv'}):return None
        elif names!={'manifest.json','live_frames.jsonl','control_events.jsonl'}:return None
        return int(timestamp)
    except (OSError,ValueError,KeyError,TypeError,AttributeError):return None


def _group_candidates(root,protected):
    groups=root/'groups'
    if not groups.exists():return []
    candidates=[]
    for directory in groups.iterdir():
        data=load_json(directory/'manifest.json')
        if data.get('phase') in ('stopping','finalizing'):continue
        if data.get('schema_version')!='group-0.1-draft':continue
        segments=data.get('segments',[])
        if not isinstance(segments,list):raise ValueError('그룹 구간 정보를 확인할 수 없습니다.')
        referenced=set()
        for segment in segments:
            if not isinstance(segment,dict):raise ValueError('그룹 구간 정보를 확인할 수 없습니다.')
            if segment.get('status')=='deleted':continue
            values=segment.get('members')
            if not isinstance(values,list) or len(values)!=2 or any(not isinstance(v,str) for v in values):
                raise ValueError('그룹 구간 연결을 확인할 수 없습니다.')
            for value in values:
                if value in referenced:raise ValueError('그룹 구간 연결이 중복됩니다.')
                referenced.add(value)
        for member in data.get('members',[]):
            value=member.get('run_directory') if isinstance(member,dict) else None
            if value is not None and value in referenced:raise ValueError('그룹 구간 연결이 중복됩니다.')
        for segment in segments:
            if not isinstance(segment,dict):raise ValueError('그룹 구간 정보를 확인할 수 없습니다.')
            if segment.get('status')!='completed' or segment.get('ledger_complete') is not True:continue
            index=segment.get('index');members=segment.get('members')
            if type(index) is not int or index<0 or not isinstance(members,list) or len(members)!=2:raise ValueError('그룹 구간 연결을 확인할 수 없습니다.')
            child=directory/'segments'/str(index)
            if segment.get('directory')!=str(child) or child.is_symlink() or not child.is_dir():raise ValueError('그룹 구간 경로를 확인할 수 없습니다.')
            files=list(child.iterdir())
            allowed={'manifest.json','alignment.jsonl','fusion_inputs.jsonl','posture_predictions.jsonl'}
            if any(p.is_symlink() or not p.is_file() or p.name not in allowed for p in files):continue
            manifest=child/'manifest.json'
            if not manifest.is_file() or manifest.stat().st_size>4*1024**2:continue
            saved=load_json(manifest)
            if (saved.get('group_id')!=directory.name or saved.get('group_segment_index')!=index
                    or saved.get('phase')!='completed' or saved.get('ledger_complete') is not True
                    or [m.get('run_directory') for m in saved.get('members',[])]!=members):continue
            paths=[];stamps=[]
            for value in members:
                if not isinstance(value,str):raise ValueError('그룹 구간 연결을 확인할 수 없습니다.')
                path=Path(value)
                if path.parent!=root or not ID.fullmatch(path.name):raise ValueError('그룹 구간 연결을 확인할 수 없습니다.')
                stamp=_candidate(path,protected,group_id=directory.name,segment_index=index)
                if stamp is None:break
                paths.append(path);stamps.append(stamp)
            if len(paths)==2 and paths[0]!=paths[1]:
                candidates.append((min(stamps),directory.name,index,child,paths))
        # The final segment uses the group root for its alignment and prediction files.
        # Remove that root only once every preceding segment has been removed.
        if (data.get('phase')=='completed' and data.get('ledger_complete') is True
                and data.get('segment_seconds') is not None
                and data.get('group_segment_index')==len(segments)):
            allowed={'manifest.json','alignment.jsonl','fusion_inputs.jsonl','posture_predictions.jsonl'}
            valid=True
            for child in directory.iterdir():
                if child.name=='segments':
                    if child.is_symlink() or not child.is_dir():valid=False;break
                elif child.is_symlink() or not child.is_file() or child.name not in allowed:valid=False;break
            members=data.get('members')
            if valid and isinstance(members,list) and len(members)==2:
                paths=[];stamps=[]
                for member in members:
                    value=member.get('run_directory') if isinstance(member,dict) else None
                    if not isinstance(value,str):break
                    path=Path(value)
                    if path.parent!=root or not ID.fullmatch(path.name):break
                    stamp=_candidate(path,protected,group_id=directory.name,segment_index=len(segments))
                    if stamp is None:break
                    paths.append(path);stamps.append(stamp)
                if len(paths)==2 and paths[0]!=paths[1]:
                    all_deleted=all(isinstance(v,dict) and v.get('status')=='deleted' for v in segments)
                    segment_dir=directory/'segments'
                    segments_empty=not segment_dir.exists() or not any(segment_dir.iterdir())
                    candidates.append((min(stamps),directory.name,None if all_deleted and segments_empty else -1,directory,paths))
        if (data.get('phase')=='completed' and data.get('final_segment_status')=='deleted'
                and all(isinstance(v,dict) and v.get('status')=='deleted' for v in segments)):
            clean=True
            for child in directory.iterdir():
                if child.name=='segments':
                    if child.is_symlink() or not child.is_dir() or any(child.iterdir()):clean=False;break
                elif child.name!='manifest.json' or child.is_symlink() or not child.is_file():clean=False;break
            stamp=data.get('final_segment_started_utc_ns')
            if clean and isinstance(stamp,str) and stamp.isascii() and stamp.isdigit():
                candidates.append((int(stamp),directory.name,-2,directory,[]))
    return candidates


def _recover(root):
    staging=root/'.delete-staging'
    if not staging.exists():return
    if staging.is_symlink() or not staging.is_dir():raise ValueError('삭제 임시 폴더를 확인하세요.')
    for transaction in staging.iterdir():
        if transaction.is_symlink() or not transaction.is_dir() or not ID.fullmatch(transaction.name):raise ValueError('삭제 임시 기록을 확인하세요.')
        intent=transaction/'intent.json'
        if intent.is_symlink() or not intent.is_file() or intent.stat().st_size>4096:raise ValueError('삭제 임시 기록을 확인하세요.')
        data=load_json(intent);gid=data.get('group_id');index=data.get('index');members=data.get('members');mode=data.get('mode','segment')
        if (mode not in ('segment','complete_group','tail_segment') or not isinstance(gid,str) or not ID.fullmatch(gid)
                or (mode=='segment' and (type(index) is not int or index<0))
                or (mode=='complete_group' and index is not None) or (mode=='tail_segment' and index!=-1)
                or not isinstance(members,list) or len(members)!=2 or len(set(members))!=2
                or any(not isinstance(v,str) or not ID.fullmatch(v) for v in members)):
            raise ValueError('삭제 임시 기록을 확인하세요.')
        if (root/'groups').is_symlink():raise ValueError('삭제 임시 그룹을 확인하세요.')
        parent=root/'groups'/gid
        if mode=='complete_group':
            marker=transaction/'committed.json'
            if marker.is_symlink():raise ValueError('삭제 임시 기록을 확인하세요.')
            if marker.exists():
                if not marker.is_file() or load_json(marker)!={'committed':True}:raise ValueError('삭제 임시 기록을 확인하세요.')
            else:
                for name,target in [('group',parent)]+[(m,root/m) for m in members]:
                    source=transaction/name
                    if source.is_symlink() or target.is_symlink() or (not source.exists() and not target.exists()):raise ValueError('임시 기록을 복구할 수 없습니다.')
                    if source.exists():
                        if target.exists():raise ValueError('임시 기록을 복구할 수 없습니다.')
                        os.replace(source,target)
            shutil.rmtree(transaction)
            continue
        if parent.is_symlink() or not parent.is_dir():raise ValueError('삭제 임시 그룹을 확인하세요.')
        manifest=parent/'manifest.json'
        if manifest.is_symlink() or not manifest.is_file():raise ValueError('삭제 임시 그룹을 확인하세요.')
        group=load_json(manifest)
        if mode=='tail_segment':
            artifacts=data.get('artifacts')
            allowed={'alignment.jsonl','fusion_inputs.jsonl','posture_predictions.jsonl'}
            if (not isinstance(artifacts,list) or len(set(artifacts))!=len(artifacts)
                    or any(not isinstance(v,str) or v not in allowed for v in artifacts)
                    or [m.get('run_directory') for m in group.get('members',[])]!=[str(root/m) for m in members]):
                raise ValueError('삭제 임시 그룹 연결을 확인하세요.')
            if group.get('final_segment_status')!='deleted':
                for name,target in [(m,root/m) for m in members]+[(n,parent/n) for n in artifacts]:
                    source=transaction/name
                    if source.is_symlink() or target.is_symlink() or (not source.exists() and not target.exists()):raise ValueError('임시 기록을 복구할 수 없습니다.')
                    if source.exists():
                        if target.exists():raise ValueError('임시 기록을 복구할 수 없습니다.')
                        os.replace(source,target)
            shutil.rmtree(transaction)
            continue
        segment=next((v for v in group.get('segments',[]) if v.get('index')==index),None)
        if not segment or segment.get('members')!=[str(root/m) for m in members]:raise ValueError('삭제 임시 그룹 연결을 확인하세요.')
        if (parent/'segments').is_symlink() or not (parent/'segments').is_dir():raise ValueError('삭제 임시 그룹을 확인하세요.')
        if segment.get('status')!='deleted':
            for name,target in [('group',parent/'segments'/str(index))]+[(m,root/m) for m in members]:
                source=transaction/name
                if source.is_symlink() or target.is_symlink() or (not source.exists() and not target.exists()):raise ValueError('임시 기록을 복구할 수 없습니다.')
                if source.exists():
                    if target.exists():raise ValueError('임시 기록을 복구할 수 없습니다.')
                    os.replace(source,target)
        shutil.rmtree(transaction)
    if not any(staging.iterdir()):staging.rmdir()


def _delete_group(root,candidate,group_objects):
    _,gid,index,child,members=candidate
    parent=root/'groups'/gid
    if index==-2:
        shutil.rmtree(parent);group_objects.pop(gid,None)
        return
    if index is None:
        staging=root/'.delete-staging';staging.mkdir(exist_ok=True)
        transaction=staging/uuid.uuid4().hex;transaction.mkdir()
        atomic_json(transaction/'intent.json',{'mode':'complete_group','group_id':gid,'index':None,'members':[m.name for m in members]})
        moved=[]
        try:
            for source,name in [(child,'group')]+[(p,p.name) for p in members]:
                target=transaction/name;os.replace(source,target);moved.append((target,source))
            atomic_json(transaction/'committed.json',{'committed':True})
        except Exception:
            for source,target in reversed(moved):os.replace(source,target)
            shutil.rmtree(transaction)
            if not any(staging.iterdir()):staging.rmdir()
            raise
        group_objects.pop(gid,None)
        shutil.rmtree(transaction)
        if not any(staging.iterdir()):staging.rmdir()
        return
    group=group_objects.get(gid)
    data=group.data if group is not None else load_json(parent/'manifest.json')
    if index==-1:
        import copy
        if data.get('phase')!='completed' or data.get('ledger_complete') is not True:raise ValueError('그룹 마지막 구간 상태가 변경됐습니다.')
        saved=copy.deepcopy(data)
        artifacts=[name for name in ('alignment.jsonl','fusion_inputs.jsonl','posture_predictions.jsonl') if (parent/name).is_file()]
        stamps=[load_json(p/'manifest.json')['started_utc_ns'] for p in members]
        staging=root/'.delete-staging';staging.mkdir(exist_ok=True)
        transaction=staging/uuid.uuid4().hex;transaction.mkdir()
        atomic_json(transaction/'intent.json',{'mode':'tail_segment','group_id':gid,'index':-1,'members':[m.name for m in members],'artifacts':artifacts})
        moved=[]
        try:
            for source,name in [(p,p.name) for p in members]+[(parent/name,name) for name in artifacts]:
                target=transaction/name;os.replace(source,target);moved.append((target,source))
            data['final_segment_status']='deleted';data['final_segment_started_utc_ns']=min(stamps,key=int)
            data['ledger_complete']=False;data['alignment_status']='deleted'
            for name in ('alignment','fusion_inputs','posture_predictions'):
                if name in data:data[name]={'status':'deleted'}
            if group is not None:group.save()
            else:atomic_json(parent/'manifest.json',data)
        except Exception:
            data.clear();data.update(saved)
            for source,target in reversed(moved):os.replace(source,target)
            shutil.rmtree(transaction)
            if not any(staging.iterdir()):staging.rmdir()
            raise
        shutil.rmtree(transaction)
        if not any(staging.iterdir()):staging.rmdir()
        return
    entry=next((v for v in data.get('segments',[]) if v.get('index')==index),None)
    if not entry or entry.get('status')!='completed':raise ValueError('그룹 구간 상태가 변경됐습니다.')
    staging=root/'.delete-staging';staging.mkdir(exist_ok=True)
    transaction=staging/uuid.uuid4().hex;transaction.mkdir()
    intent={'group_id':gid,'index':index,'members':[m.name for m in members]}
    atomic_json(transaction/'intent.json',intent)
    moved=[]
    try:
        for source,name in [(child,'group')]+[(p,p.name) for p in members]:
            target=transaction/name;os.replace(source,target);moved.append((target,source))
        entry['status']='deleted'
        try:
            if group is not None:group.save()
            else:atomic_json(parent/'manifest.json',data)
        except Exception:
            entry['status']='completed'
            raise
    except Exception:
        for source,target in reversed(moved):os.replace(source,target)
        shutil.rmtree(transaction)
        if not any(staging.iterdir()):staging.rmdir()
        raise
    shutil.rmtree(transaction)
    if not any(staging.iterdir()):staging.rmdir()


def reclaim(output_root,max_bytes,protected=(),group_objects=None):
    """Remove oldest eligible segments until under limit; never remove other records."""
    if type(max_bytes) is not int or max_bytes<1:raise ValueError('저장 용량을 확인하세요.')
    root=blackbox_root(output_root)
    if root.exists():_recover(root)
    total=_size(root)
    if total<=max_bytes:return {'bytes':total,'deleted':[],'within_limit':True}
    protected=set(protected)|_group_members(root)
    deleted=[];skipped=set()
    while total>max_bytes:
        candidates=[]
        for directory in root.iterdir():
            stamp=_candidate(directory,protected)
            if stamp is not None and ('single',directory.name) not in skipped:
                candidates.append((stamp,'single',directory.name,directory))
        for candidate in _group_candidates(root,protected):
            record_id=candidate[1]+':'+str(candidate[2])
            if ('group',record_id) not in skipped:candidates.append((candidate[0],'group',record_id,candidate))
        if not candidates:break
        _,kind,record_id,value=min(candidates)
        if kind=='group':
            candidate=value
            # Revalidate pin/open state and paths immediately before moving a unit.
            index=candidate[2]
            if index is None or index==-1:
                data=load_json(candidate[3]/'manifest.json');index=data.get('group_segment_index')
            if any(_candidate(p,protected,group_id=candidate[1],segment_index=index) is None for p in candidate[4]):
                skipped.add((kind,record_id));continue
            _delete_group(root,candidate,group_objects or {})
            total=_size(root);deleted.append(record_id)
            continue
        directory=value
        # The active/reader and root locks held by the caller prevent in-process races.
        if _candidate(directory,protected) is None:
            skipped.add((kind,record_id));continue
        shutil.rmtree(directory)
        total=_size(root);deleted.append(record_id)
    return {'bytes':total,'deleted':deleted,'within_limit':total<=max_bytes}
