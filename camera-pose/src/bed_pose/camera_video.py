"""Optional video of analysis frames, linked one-to-one to the live JSONL ledger."""
from fractions import Fraction
from pathlib import Path
import os
import av
from .contracts import IntegrityError
from .io_utils import file_hash, json_text, strict_loads

VIDEO='analysis_video.mkv'
INDEX='video_frames.jsonl'


class CameraVideo:
    def __init__(self, directory, width, height, started_ns):
        self.root=Path(directory)
        self.started=int(started_ns)
        self.index=(self.root/INDEX).open('x',encoding='utf-8',newline='\n')
        self.container=None
        try:
            self.container=av.open(str(self.root/VIDEO),mode='w',format='matroska')
            self.stream=self.container.add_stream('ffv1',rate=30)
            self.stream.width=width;self.stream.height=height
            self.stream.pix_fmt='bgr0'
            self.stream.time_base=Fraction(1,1000)
            self.stream.codec_context.time_base=Fraction(1,1000)
        except Exception:
            if self.container:self.container.close()
            self.index.close()
            raise
        self.count=0;self.previous=-1

    def frame(self, rgb, row, frame_id):
        received=int(row['received_ns'])
        # Matroska timestamp granularity is milliseconds. Preserve exact receive time in index.
        pts=max((received-self.started)//1_000_000,self.previous+1,0)
        frame=av.VideoFrame.from_ndarray(rgb,format='rgb24')
        frame.pts=pts;frame.time_base=Fraction(1,1000)
        for packet in self.stream.encode(frame):self.container.mux(packet)
        record={'frame_id':frame_id,'stream_frame_id':row['stream_frame_id'],
                'received_ns':row['received_ns'],'video_pts_ms':pts}
        self.index.write(json_text(record)+'\n')
        self.count+=1;self.previous=pts

    def finish(self):
        try:
            for packet in self.stream.encode():self.container.mux(packet)
            self.container.close()
            self.index.flush();os.fsync(self.index.fileno())
        finally:
            self.index.close()
        return {'version':'analysis-video-0.1','status':'recorded' if self.count else 'empty',
                'path':VIDEO if self.count else None,'index_path':INDEX,
                'video_sha256':file_hash(self.root/VIDEO) if self.count else None,'index_sha256':file_hash(self.root/INDEX),
                'frame_count':self.count,'codec':'ffv1','time_base':'1/1000',
                'pts_origin':'measurement_started_ns','time_basis':'frame_receive_time_approximation',
                'coverage':'analysis_frames_only','exposure_synchronized':False}


def verify_video(directory, manifest):
    root=Path(directory);info=manifest.get('video_artifact')
    if not isinstance(info,dict) or info.get('version')!='analysis-video-0.1' or info.get('index_path')!=INDEX:
        raise IntegrityError('Invalid video metadata')
    if (info.get('frame_count')!=manifest['recorded_frames'] or info.get('coverage')!='analysis_frames_only'
            or info.get('time_base')!='1/1000' or info.get('pts_origin')!='measurement_started_ns'
            or info.get('time_basis')!='frame_receive_time_approximation' or info.get('exposure_synchronized') is not False
            or info.get('codec')!='ffv1'):
        raise IntegrityError('Invalid video timing metadata')
    empty=info['frame_count']==0
    if (info.get('status'),info.get('path'),info.get('video_sha256')) != (('empty',None,None) if empty else ('recorded',VIDEO,info.get('video_sha256'))):
        raise IntegrityError('Invalid video artifact state')
    names=(INDEX,) if empty else (VIDEO,INDEX)
    if any((root/name).is_symlink() or (root/name).resolve().parent!=root.resolve() for name in names):
        raise IntegrityError('Invalid video artifact path')
    if (not empty and file_hash(root/VIDEO)!=info.get('video_sha256')) or file_hash(root/INDEX)!=info.get('index_sha256'):
        raise IntegrityError('Video artifact checksum mismatch')
    with (root/INDEX).open(encoding='utf-8') as indices, (root/'live_frames.jsonl').open(encoding='utf-8') as rows:
        previous=-1;count=0
        for raw in rows:
            line=indices.readline()
            if not line:raise IntegrityError('Missing video frame reference')
            item=strict_loads(line);row=strict_loads(raw)
            expected=max((int(row['received_ns'])-int(manifest['started_ns']))//1_000_000,previous+1,0)
            if item!={'frame_id':count,'stream_frame_id':row['stream_frame_id'],'received_ns':row['received_ns'],'video_pts_ms':expected}:
                raise IntegrityError('Video and log frame references differ')
            previous=expected;count+=1
        if indices.readline() or count!=info['frame_count']:raise IntegrityError('Video frame count mismatch')
    if empty:return
    # Decode to verify count, dimensions, codec and timestamps, including muxer time-base changes.
    with av.open(str(root/VIDEO)) as container, (root/INDEX).open(encoding='utf-8') as indices:
        if len(container.streams.video)!=1 or container.streams.video[0].codec_context.name!='ffv1':
            raise IntegrityError('Video stream is invalid')
        count=0
        for frame in container.decode(video=0):
            line=indices.readline()
            if not line:raise IntegrityError('Extra video frame')
            item=strict_loads(line)
            if (frame.width!=manifest['width'] or frame.height!=manifest['height'] or frame.pts is None
                    or Fraction(frame.pts)*frame.time_base != Fraction(item['video_pts_ms'],1000)):
                raise IntegrityError('Video frame geometry or timestamp mismatch')
            count+=1
        if indices.readline() or count!=info['frame_count']:raise IntegrityError('Video decode count mismatch')
