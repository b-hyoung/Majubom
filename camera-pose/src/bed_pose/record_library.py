"""Read-only, root-confined browsing of original live measurement records."""
from array import array
from collections import OrderedDict
from pathlib import Path
from .pose_display import display_people
import re
import threading
import os
import uuid

from .contracts import IntegrityError
from .io_utils import file_hash,load_json,strict_loads
from .live_recording import FILES,SUPPORTED_SCHEMAS,verify_live

ID=re.compile(r'[0-9a-f]{32}')


def pagination(offset,limit):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('페이지 위치는 0 이상, 한 페이지는 1~100개여야 합니다.')


def confined_files(directory):
    for name in ('manifest.json',)+FILES:
        path=directory/name
        if path.is_symlink() or path.resolve().parent!=directory or not path.is_file():
            raise ValueError('기록 파일이 없거나 지원하지 않는 경로입니다.')
        if name=='manifest.json' and path.stat().st_size>4*1024**2:
            raise ValueError('기록 요약 파일이 지원 범위를 넘습니다.')


def metadata(manifest,record_id):
    if (not isinstance(manifest,dict) or manifest.get('measurement_id')!=record_id
            or manifest.get('schema_version') not in SUPPORTED_SCHEMAS):
        raise ValueError('지원하는 측정 기록이 아닙니다.')
    finished=manifest.get('ledger_complete') is True and manifest.get('run_status') in ('succeeded','failed')
    source=manifest.get('source_profile') or {}
    if (not isinstance(source,dict) or type(manifest.get('recorded_frames')) is not int
            or manifest['recorded_frames']<0 or manifest.get('run_status') not in ('recording','succeeded','failed')):
        raise ValueError('기록 요약 형식이 맞지 않습니다.')
    utc=manifest.get('started_utc_ns')
    if not isinstance(utc,str) or len(utc)>24 or not utc.isascii() or not utc.isdigit():utc=None
    return {'record_id':record_id,'started_utc_ns':utc,
            'source_kind':source.get('kind') if source.get('kind') in ('camera','video_file','synthetic') else None,
            'execution_kind':manifest.get('execution_kind') if manifest.get('execution_kind') in ('model_inference','synthetic') else None,
            'run_status':manifest.get('run_status'),'recorded_frames':manifest.get('recorded_frames'),
            'classifier_id':manifest.get('posture_classifier') if isinstance(manifest.get('posture_classifier'),str) else None,
            'availability':'ready' if finished else 'incomplete',
            'integrity':'not_checked','video_recording':manifest.get('video_recording',False)}


class OpenRecord:
    def __init__(self,directory,record_id):
        self.directory=directory
        confined_files(directory)
        initial_hash=file_hash(directory/'manifest.json')
        verify_live(directory)  # Pure replay/checks only; no SDK or source video.
        self.manifest=load_json(directory/'manifest.json')
        self.manifest_hash=initial_hash
        self.meta=metadata(self.manifest,record_id)
        if self.meta['availability']!='ready':raise ValueError('측정 종료 후 기록을 열 수 있습니다.')
        self.check()
        self.offsets=array('Q')
        with (directory/'live_frames.jsonl').open('rb') as stream:
            while True:
                position=stream.tell()
                if not stream.readline():break
                self.offsets.append(position)
        if len(self.offsets)!=self.manifest['recorded_frames']:raise ValueError('기록 프레임 수가 변경되었습니다.')
        self.check()
        self.timeline_cache=OrderedDict()
        self.timeline_lock=threading.Lock()
        self.image_lock=threading.Lock()

    def check(self):
        confined_files(self.directory)
        if (file_hash(self.directory/'manifest.json')!=self.manifest_hash or any(
                file_hash(self.directory/name)!=self.manifest['artifact_sha256'][name] for name in FILES)):
            raise ValueError('열어 둔 기록이 변경되었습니다. 기록 목록에서 다시 확인하세요.')

    def page(self,offset,limit):
        pagination(offset,limit);self.check()
        rows=[]
        with (self.directory/'live_frames.jsonl').open('rb') as stream:
            for i in range(offset,min(offset+limit,len(self.offsets))):
                stream.seek(self.offsets[i]);row=strict_loads(stream.readline().decode('utf-8'))
                a=row.get('posture_assessment')
                assessment=None if a is None else {k:a.get(k) for k in (
                    'status','label','reasons','candidate_id','track_id','classifier_id','quality_status')}
                orientation=(a or {}).get('rule_features',{}).get('orientation_decision',{})
                rows.append({'frame_id':row['frame_id'],'stream_frame_id':row['stream_frame_id'],
                    'clock_id':row['clock_id'],'received_ns':row['received_ns'],
                    'received_elapsed_ns':str(int(row['received_ns'])-int(self.manifest['started_ns'])),
                    'source_media':row.get('source_media'),'processing_status':row['processing_status'],
                    'candidate_count':row['candidate_count'],'assessment':assessment,
                    'score_decision':(a or {}).get('rule_features',{}).get('score_decision'),
                    'body_assessment':(a or {}).get('rule_features',{}).get('body_assessment'),
                    'body_state':(a or {}).get('rule_features',{}).get('body_state'),
                    'stable_posture':row.get('stable_posture'),
                    'decision_evidence':row.get('decision_evidence'),
                    'bed_relation':(a or {}).get('rule_features',{}).get('bed_relation'),
                    'context_assessment':(a or {}).get('rule_features',{}).get('context_assessment'),
                    'silhouette_assessment':row.get('silhouette_assessment'),
                    'decision_source':orientation.get('source'),'error':row.get('error')})
        self.check()
        return {'record_id':self.meta['record_id'],'offset':offset,'limit':limit,
                'total':len(self.offsets),'frames':rows}

    def image(self,frame_id,video):
        with self.image_lock:return self._image(frame_id,video)

    def _image(self,frame_id,video):
        from .live_sources import VideoFileSource
        from .preview_quality import encode_preview
        if type(frame_id) is not int or not 0<=frame_id<len(self.offsets):
            raise ValueError('기록 프레임 번호를 확인하세요.')
        recorded=bool(self.meta.get('video_recording'))
        if self.meta['source_kind']!='video_file' and not recorded:
            raise ValueError('이 측정에는 녹화된 영상이 없습니다.')
        self.check()
        with (self.directory/'live_frames.jsonl').open('rb') as stream:
            stream.seek(self.offsets[frame_id]);row=strict_loads(stream.readline().decode('utf-8'))
        media=row.get('source_media')
        if recorded:
            import av
            from .camera_video import VIDEO,INDEX
            artifact=self.manifest['video_artifact']
            if any((self.directory/name).is_symlink() or (self.directory/name).resolve().parent!=self.directory for name in (VIDEO,INDEX)):
                raise ValueError('녹화 파일 경로가 올바르지 않습니다.')
            if file_hash(self.directory/VIDEO)!=artifact['video_sha256'] or file_hash(self.directory/INDEX)!=artifact['index_sha256']:
                raise ValueError('녹화 영상 또는 프레임 인덱스가 변경되었습니다.')
            with (self.directory/INDEX).open(encoding='utf-8') as stream:
                for _ in range(frame_id+1):item=strict_loads(next(stream))
            if item['frame_id']!=frame_id or item['stream_frame_id']!=row['stream_frame_id'] or item['received_ns']!=row['received_ns']:
                raise ValueError('영상과 로그 프레임이 일치하지 않습니다.')
            with av.open(str(self.directory/VIDEO)) as container:
                for i,frame in enumerate(container.decode(video=0)):
                    if i==frame_id:break
                else:raise ValueError('녹화 영상에서 프레임을 찾지 못했습니다.')
                from fractions import Fraction
                if frame.pts is None or frame.pts*frame.time_base != Fraction(item['video_pts_ms'],1000):
                    raise ValueError('녹화 영상의 프레임 시각이 일치하지 않습니다.')
                rgb=frame.to_ndarray(format='rgb24')
            image,preview=encode_preview(rgb,'high')
        else:
            if not media or type(media.get('source_frame_id')) is not int or media['source_frame_id']<0:
                raise ValueError('이 기록에는 원본 프레임 식별 정보가 없습니다.')
            if video is None:raise ValueError('대조할 원본 영상 파일을 선택하세요.')
            source=VideoFileSource(video)
            try:
                profile=source.open()
                if profile['sha256']!=self.manifest['source_profile'].get('sha256'):
                    raise ValueError('측정에 사용한 원본 영상과 파일 해시가 다릅니다. 원본을 다시 선택하세요.')
                for _ in range(media['source_frame_id']+1):rgb=source.read()
                if source.media!=media or rgb.shape[:2]!=(self.manifest['height'],self.manifest['width']):
                    raise ValueError('원본 프레임의 시각·픽셀·크기가 기록과 다릅니다. 현재 디코더로 대조할 수 없습니다.')
                image,preview=encode_preview(rgb,'high')
            except StopIteration as exc:
                raise ValueError('기록에 대응하는 원본 프레임을 찾지 못했습니다.') from exc
            finally:source.close()
        region=self.manifest['initial_bed_region']
        with (self.directory/'control_events.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                event=strict_loads(line)
                if event['type']=='bed_region_changed' and event['revision']==row['bed_region_revision']:region=event['region']
        self.check()
        return {'record_id':self.meta['record_id'],'frame_id':frame_id,
                'stream_frame_id':row['stream_frame_id'],'source_media':media,
                'width':self.manifest['width'],'height':self.manifest['height'],
                'image':image,'preview':preview,'people':display_people(row,self.manifest['width'],self.manifest['height']),
                'stable_posture':row.get('stable_posture'),
                'decision_evidence':row.get('decision_evidence'),
                'native_pose_cues':row.get('native_pose_cues'),
                'bed_region':region,'posture_assessment':row.get('posture_assessment'),
                'silhouette_assessment':row.get('silhouette_assessment'),
                'verification':'recorded_video_frame_timestamp_verified' if recorded else 'source_sha256_pts_pixels_geometry_verified',
                'inference_performed':False,'full_silhouette_available':False}

    def timeline(self,basis,bins):
        from .record_timeline import summarize
        with self.timeline_lock:
            self.check()
            key=(basis,bins)
            if key not in self.timeline_cache:
                def rows():
                    with (self.directory/'live_frames.jsonl').open(encoding='utf-8') as stream:
                        for line in stream:yield strict_loads(line)
                summary=summarize(rows,self.manifest,basis,bins)
                self.check()
                self.timeline_cache[key]=dict(summary,record_id=self.meta['record_id'])
                while len(self.timeline_cache)>4:self.timeline_cache.popitem(last=False)
            self.timeline_cache.move_to_end(key)
            self.check()
            return self.timeline_cache[key]


class RecordLibrary:
    def __init__(self,root):
        self.root=Path(root).resolve();self.opened=OrderedDict();self.lock=threading.RLock()

    def directory(self,record_id):
        if not isinstance(record_id,str) or ID.fullmatch(record_id) is None:
            raise ValueError('기록 목록에서 측정을 선택하세요.')
        path=self.root/record_id
        if path.is_symlink() or path.resolve().parent!=self.root or not path.is_dir():
            raise ValueError('측정 기록을 찾을 수 없습니다.')
        return path.resolve()

    def protection_paths(self,directory,manifest):
        """Return the two recorded camera directories when their group is known."""
        segment=manifest.get('blackbox_segment') or {}
        gid=segment.get('group_id');index=segment.get('index')
        if not isinstance(gid,str) or ID.fullmatch(gid) is None or type(index) is not int:return [directory]
        if (self.root/'groups').is_symlink():return [directory]
        group=self.root/'groups'/gid
        summary=group/'manifest.json'
        if group.is_symlink() or summary.is_symlink() or not summary.is_file() or summary.stat().st_size>4*1024**2:return [directory]
        try:
            data=load_json(summary)
            if data.get('group_id')!=gid:return [directory]
            entry=next((s for s in data.get('segments',[]) if s.get('index')==index),None)
            values=entry.get('members') if entry else [m.get('run_directory') for m in data.get('members',[])]
            if not isinstance(values,list) or len(values)!=2:return [directory]
            paths=[]
            for value in values:
                if not isinstance(value,str):return [directory]
                path=Path(value)
                if path.parent!=self.root or ID.fullmatch(path.name) is None:return [directory]
                path=self.directory(path.name)
                source=path/'manifest.json'
                if source.is_symlink() or not source.is_file() or source.stat().st_size>4*1024**2:return [directory]
                other=load_json(source)
                part=other.get('blackbox_segment') or {}
                if part.get('group_id')!=gid or part.get('index')!=index:return [directory]
                paths.append(path)
            return paths if len(set(paths))==2 and directory in paths else [directory]
        except (OSError,ValueError,TypeError,KeyError,AttributeError):return [directory]

    def catalog(self,offset=0,limit=50):
        pagination(offset,limit);items=[]
        for path in self.root.iterdir() if self.root.is_dir() else []:
            if ID.fullmatch(path.name) is None:continue
            try:
                directory=self.directory(path.name)
                if (directory/'manifest.json').is_symlink() or (directory/'manifest.json').stat().st_size>4*1024**2:
                    raise ValueError('Unsupported manifest')
                manifest=load_json(directory/'manifest.json')
                item=metadata(manifest,path.name)
                item['protected']=any((p/'.keep').exists() or (p/'.keep').is_symlink() for p in self.protection_paths(directory,manifest))
            except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):
                item={'record_id':path.name,'availability':'invalid','integrity':'not_checked'}
            items.append(item)
        def order(item):
            value=item.get('started_utc_ns')
            stamp=int(value) if isinstance(value,str) and value.isascii() and value.isdigit() else 0
            return stamp,item['record_id']
        items.sort(key=order,reverse=True)
        return {'items':items[offset:offset+limit],'offset':offset,'limit':limit,'total':len(items)}

    def open(self,record_id):
        with self.lock:
            try:
                record=OpenRecord(self.directory(record_id),record_id)
            except (IntegrityError,OSError,KeyError,TypeError) as exc:
                raise ValueError('기록 검사에 실패했습니다. 파일 누락·변경·지원 형식을 확인하세요.') from exc
            record.lease_id=uuid.uuid4().hex
            self.opened[record_id]=record;self.opened.move_to_end(record_id)
            while len(self.opened)>4:self.opened.popitem(last=False)
            protected=any((p/'.keep').exists() or (p/'.keep').is_symlink() for p in self.protection_paths(record.directory,record.manifest))
        # Return original settings for inspection, never for live reconfiguration.
        return dict(record.meta,integrity='verified',counts=record.manifest['counts'],protected=protected,lease_id=record.lease_id,
                    automatic_profile=record.manifest.get('automatic_posture_profile'),
                    stability_profile=record.manifest.get('stability_profile'),
                    score_semantics='independent_rule_fit_not_probability',
                    content='stored_frame_results_no_video_replay')

    def protect(self,record_id,protected):
        if type(protected) is not bool:raise ValueError('보관 설정을 확인하세요.')
        with self.lock:
            directory=self.directory(record_id)
            manifest=load_json(directory/'manifest.json')
            if manifest.get('storage_class')!='blackbox' or manifest.get('ledger_complete') is not True:
                raise ValueError('완료된 블랙박스 기록만 보관 설정할 수 있습니다.')
            markers=[p/'.keep' for p in self.protection_paths(directory,manifest)]
            if any(m.is_symlink() or (m.exists() and not m.is_file()) for m in markers):raise ValueError('보관 설정 파일을 확인하세요.')
            for marker in markers:
                if protected:
                    flags=os.O_WRONLY|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)
                    fd=os.open(marker,flags,0o600)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                else:marker.unlink(missing_ok=True)
            return {'record_id':record_id,'protected':protected}

    def release(self,record_id,lease_id):
        with self.lock:
            record=self.opened.get(record_id)
            if record is not None and record.lease_id==lease_id:self.opened.pop(record_id,None)

    def frames(self,record_id,offset=0,limit=50):
        with self.lock:
            self.directory(record_id)
            record=self.opened.get(record_id)
            if record is None:raise ValueError('기록 목록에서 측정을 먼저 여세요.')
            return record.page(offset,limit)

    def timeline(self,record_id,basis=None,bins=240):
        with self.lock:
            self.directory(record_id)
            record=self.opened.get(record_id)
            if record is None:raise ValueError('기록 목록에서 측정을 먼저 여세요.')
            if basis is None:basis='media' if record.meta['source_kind']=='video_file' else 'receive'
            return record.timeline(basis,bins)

    def image(self,record_id,frame_id,video=None):
        with self.lock:
            self.directory(record_id)
            record=self.opened.get(record_id)
            if record is None:raise ValueError('기록 목록에서 측정을 먼저 여세요.')
            return record.image(frame_id,video)
