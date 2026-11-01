"""Legacy Tk panel helpers. The public launcher now opens the HTML workspace."""
import base64
from pathlib import Path
import queue
import time
from .rendering import COLORS, EDGES, png_bytes


def viewport(width, height, image_width, image_height):
    scale = min(width/image_width, height/image_height)
    return scale, (width-image_width*scale)/2, (height-image_height*scale)/2


def source_point(x, y, width, height, image_width, image_height, clamp=False):
    scale, dx, dy = viewport(width, height, image_width, image_height)
    px, py = (x-dx)/scale, (y-dy)/scale
    if clamp:
        return max(0, min(image_width, px)), max(0, min(image_height, py))
    if not 0 <= px <= image_width or not 0 <= py <= image_height:
        return None
    return px, py


class LiveWindow:
    def __init__(self, root, worker, parent=None, title='입력'):
        import tkinter as tk
        from tkinter import ttk, font
        self.tk, self.ttk, self.root, self.worker = tk, ttk, root, worker
        self.owns_window = parent is None
        self.is_file = bool(getattr(worker.engine.source, 'is_file', False))
        self.playback_paused = self.is_file
        families = {name.casefold(): name for name in font.families(root)}
        choices = ('Malgun Gothic','맑은 고딕','Noto Sans CJK KR','Noto Sans CJK SC','Unifont')
        self.font_family = next((families[name.casefold()] for name in choices if name.casefold() in families),
                                font.nametofont('TkDefaultFont').actual('family'))
        self.frame = self.region = self.drag_start = self.drag_end = None
        self.geometry = None
        self.drag_frame_id = None
        self.pending, self.recording, self.closed, self.closing, self.had_error = False, False, False, False, False
        self.close_started = None
        self.last_error = None
        self.ended = False
        self.record_count = 0
        self.measure_started_ns = None
        self.measure_elapsed = 0.0
        if self.owns_window:
            root.title('BedPose · 측정 작업실')
            root.geometry('1220x820')
            root.minsize(980, 700)
        style = ttk.Style(root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TFrame', background='#edf2f7')
        style.configure('TLabel', background='#edf2f7', foreground='#1e293b', font=(self.font_family, 10))
        style.configure('TButton', font=(self.font_family, 10), padding=(12, 8))
        style.configure('Primary.TButton', background='#1264d1', foreground='white')
        style.map('Primary.TButton', background=[('active','#0955ba'),('disabled','#cbd5e1')])
        style.configure('TCheckbutton', font=(self.font_family, 10), background='#edf2f7')
        style.configure('Card.TFrame', background='white')
        style.configure('Card.TLabel', background='white', foreground='#526176', font=(self.font_family,10))
        style.configure('Value.TLabel', background='white', foreground='#172b4d', font=(self.font_family,18,'bold'))
        style.configure('TNotebook.Tab', padding=(14,8), font=(self.font_family,10))
        outer = ttk.Frame(parent or root, padding=14)
        outer.pack(fill='both', expand=True)
        self.outer = outer
        self.kind = tk.StringVar(value=title+' · 연결 중')
        ttk.Label(outer, textvariable=self.kind, font=(self.font_family,12,'bold')).grid(row=0,column=0,sticky='w')
        self.status = tk.StringVar(value='입력과 모델을 준비하고 있습니다.')
        ttk.Label(outer, textvariable=self.status, wraplength=1000).grid(row=1,column=0,sticky='w',pady=(4,10))
        body = ttk.Frame(outer)
        outer.columnconfigure(0,weight=1)
        outer.rowconfigure(2,weight=1)
        body.grid(row=2,column=0,sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, background='#101d30', highlightthickness=0, width=640, height=360)
        self.canvas.grid(row=0,column=0,sticky='nsew',padx=(0,14))
        self.canvas.create_text(24,24,anchor='nw',text='입력 연결 후 영상이 표시됩니다',fill='#cbd5e1')
        self.canvas.bind('<Configure>', lambda event: self.draw())
        self.canvas.bind('<ButtonPress-1>', self.drag_begin)
        self.canvas.bind('<B1-Motion>', self.drag_move)
        self.canvas.bind('<ButtonRelease-1>', self.drag_finish)
        side = ttk.Frame(body, width=280)
        side.grid(row=0,column=1,sticky='ns')
        self.posture = tk.StringVar(value='판정 기능 준비 중')
        self.detection = tk.StringVar(value='입력 대기')
        self.bed_status = tk.StringVar(value='영역 미지정')
        self.event_status = tk.StringVar(value='판정 기능 준비 중')
        self.cards = []
        self.details = []
        for heading,value,detail in (
            ('AI 자세 판정', self.posture, '분류기 연결 후 자세명을 표시합니다.'),
            ('관절 검출', self.detection, '관절 후보는 자세 판정이나 정확도가 아닙니다.'),
            ('침대 영역', self.bed_status, '영상에서 두 모서리를 드래그하세요.')):
            card=ttk.Frame(side,style='Card.TFrame',padding=12)
            card.pack(fill='x',pady=(0,10))
            ttk.Label(card,text=heading,style='Card.TLabel').pack(anchor='w')
            ttk.Label(card,textvariable=value,style='Value.TLabel').pack(anchor='w',pady=(6,6))
            description=ttk.Label(card,text=detail,style='Card.TLabel',wraplength=250)
            description.pack(anchor='w')
            self.details.append(description)
            self.cards.append(card)
        ttk.Label(side,text='이탈 · 복귀: 판정 기능 준비 중',wraplength=265).pack(anchor='w',pady=4)
        ttk.Label(outer,text='① 입력 확인   →   ② 침대 영역 지정   →   ③ 측정 시작',font=(self.font_family,10,'bold')).grid(row=3,column=0,sticky='w',pady=(10,6))
        controls = ttk.Frame(outer)
        controls.grid(row=4,column=0,sticky='ew')
        self.start_button = ttk.Button(controls, text='측정 시작', style='Primary.TButton', command=self.start)
        self.start_button.pack(side='left', padx=(0,8))
        self.stop_button = ttk.Button(controls, text='측정 종료', command=self.stop)
        self.stop_button.pack(side='left', padx=(0,8))
        self.clear_button = ttk.Button(controls, text='영역 지우기', command=self.clear_region)
        self.clear_button.pack(side='left', padx=(0,12))
        self.overlay = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls,text='관절 표시',variable=self.overlay,command=self.draw).pack(side='left')
        if self.is_file:
            self.play_button = ttk.Button(controls,text='재생',command=self.toggle_playback)
            self.play_button.pack(side='left',padx=8)
        ttk.Button(controls,text='입력 연결 종료',command=self.quit).pack(side='right')
        self.measure_status = tk.StringVar(value='기록 대기 · 원본 영상 저장 꺼짐')
        ttk.Label(outer,textvariable=self.measure_status,font=(self.font_family,11,'bold')).grid(row=5,column=0,sticky='w',pady=(8,3))
        self.summary = tk.StringVar(value='프레임 수신 대기')
        ttk.Label(outer,textvariable=self.summary,wraplength=1000).grid(row=6,column=0,sticky='w',pady=(0,6))
        self.path = tk.StringVar(value='측정을 시작하면 새 결과 폴더가 생성됩니다.')
        self.path_entry=ttk.Entry(outer,textvariable=self.path,state='readonly')
        self.path_entry.grid(row=7,column=0,sticky='ew')
        outer.bind('<Configure>',self.compact_layout)
        self.update_controls()
        if self.owns_window:
            root.protocol('WM_DELETE_WINDOW', self.quit)
        root.after(30, self.poll)

    def compact_layout(self,event):
        if event.widget != self.outer:
            return
        for card in self.cards:
            card.configure(padding=8 if event.height < 570 else 12)
        for detail in self.details:
            if event.height < 570:
                detail.pack_forget()
            elif not detail.winfo_manager():
                detail.pack(anchor='w')

    def toggle_playback(self):
        self.worker.submit('playback', paused=not self.playback_paused)

    def show_observation(self, row):
        # This release has no classifier. Never derive posture from candidate count.
        self.posture.set('판정 기능 준비 중')
        count = row['candidate_count']
        self.detection.set('후보 없음' if count == 0 else f'후보 {count}명')
        self.bed_status.set('영역 지정됨' if self.region else '영역 미지정')

    def update_controls(self):
        active = not self.closed and not self.closing and not self.pending
        self.start_button.configure(state='normal' if active and self.frame is not None and self.region is not None and not self.recording else 'disabled')
        self.stop_button.configure(state='normal' if active and self.recording else 'disabled')
        self.clear_button.configure(state='normal' if active and self.frame is not None else 'disabled')
        if self.is_file:
            self.play_button.configure(state='normal' if active and self.frame is not None else 'disabled')

    def start(self):
        self.pending = True
        self.status.set('측정 기록을 준비하고 있습니다.')
        self.measure_status.set('시작 준비 중')
        self.update_controls()
        self.worker.submit('start')

    def stop(self):
        self.pending = True
        self.status.set('기록을 저장하고 검사하고 있습니다.')
        self.measure_status.set('기록 정리 중')
        self.update_controls()
        self.worker.submit('stop')

    def clear_region(self):
        if self.geometry:
            self.worker.submit('region', box=None, width=self.geometry[0], height=self.geometry[1], selected_frame_id=self.frame['row']['stream_frame_id'])

    def drag_begin(self, event):
        if self.frame is None or self.closed or self.closing:
            return
        self.drag_start = source_point(event.x, event.y, max(1, self.canvas.winfo_width()),
            max(1, self.canvas.winfo_height()), *self.geometry)
        self.drag_end = self.drag_start
        self.drag_frame_id = self.frame['row']['stream_frame_id']

    def drag_move(self, event):
        if self.drag_start is not None:
            self.drag_end = source_point(event.x, event.y, max(1, self.canvas.winfo_width()),
                max(1, self.canvas.winfo_height()), *self.geometry, clamp=True)
            self.draw()

    def drag_finish(self, event):
        self.drag_move(event)
        if self.drag_start is not None and self.drag_end is not None:
            x1, y1 = self.drag_start
            x2, y2 = self.drag_end
            if x1 != x2 and y1 != y2:
                self.worker.submit('region', box=[min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2)],
                                   width=self.geometry[0], height=self.geometry[1], selected_frame_id=self.drag_frame_id)
                self.status.set('침대 영역을 적용하고 있습니다.')
        self.drag_start = self.drag_end = None
        self.draw()

    def draw(self):
        if self.frame is None:
            return
        import cv2
        rgb = self.frame['rgb']
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        scale, dx, dy = viewport(width, height, *self.geometry)
        image = cv2.resize(rgb, (max(1, round(self.geometry[0]*scale)), max(1, round(self.geometry[1]*scale))))
        self.photo = self.tk.PhotoImage(data=base64.b64encode(png_bytes(image)).decode('ascii'), format='png')
        self.canvas.delete('all')
        self.canvas.create_image(dx, dy, anchor='nw', image=self.photo)
        if self.overlay.get():
            for index, person in enumerate(self.frame['row']['mapped_people'] or []):
                points = {i:(dx+j['x_px']*scale, dy+j['y_px']*scale) for i,j in enumerate(person['joints'])
                          if j['availability']=='predicted' and 0<=j['x_px']<self.geometry[0] and 0<=j['y_px']<self.geometry[1]}
                color = COLORS[index%len(COLORS)]
                for a,b in EDGES:
                    if a in points and b in points:
                        self.canvas.create_line(*points[a], *points[b], fill=color, width=2)
                for x,y in points.values():
                    self.canvas.create_oval(x-3,y-3,x+3,y+3,fill=color,outline='#0c1420')
        box = None
        if self.drag_start is not None and self.drag_end is not None:
            box = (*self.drag_start, *self.drag_end)
        elif self.region is not None:
            box = [self.region[k] for k in ('x1','y1','x2','y2')]
        if box is not None:
            self.canvas.create_rectangle(dx+box[0]*scale,dy+box[1]*scale,dx+box[2]*scale,dy+box[3]*scale,
                                         outline='#5da9ff',width=3,dash=(8,4))
        if self.worker.engine.execution_kind == 'synthetic':
            self.canvas.create_text(16,16,anchor='nw',text='합성 입력 · 실제 카메라/인식 아님',fill='white',font=(self.font_family,12,'bold'))

    def poll(self):
        try:
            while True:
                kind, value = self.worker.messages.get_nowait()
                if kind == 'ready':
                    self.kind.set('합성 입력 · 실제 인식 아님' if self.worker.engine.execution_kind=='synthetic' else ('저장 영상 · 처리 속도로 재생' if self.is_file else f'카메라 {self.worker.engine.source.index} · 모델 출력'))
                    self.status.set('영상을 확인하고 침대 영역을 드래그로 지정하세요.')
                elif kind == 'region':
                    self.region = value['region']
                    self.bed_status.set('영역 지정됨' if self.region else '영역 미지정')
                    self.status.set('침대 영역을 수동 지정했습니다.' if self.region else '침대 영역 미지정 · 침대 관련 판정은 제공하지 않습니다.')
                elif kind == 'started':
                    self.recording, self.pending = True, False
                    self.record_count = 0
                    self.measure_started_ns = int(value['started_ns'])
                    self.measure_elapsed = 0.0
                    self.measure_status.set('측정 중 · 영상 저장 꺼짐')
                    self.path.set(value['directory'])
                    self.status.set('측정 중 · 관절과 시각을 기록합니다. 영상은 저장하지 않습니다.')
                elif kind == 'stopped':
                    self.recording, self.pending = False, False
                    self.path.set(value['directory'])
                    manifest = value['manifest']
                    self.record_count = manifest['recorded_frames']
                    self.measure_elapsed = (int(manifest['finished_ns'])-int(manifest['started_ns']))/1e9
                    self.measure_status.set('기록 검사 완료' if manifest['run_status']=='succeeded' else '기록 오류')
                    self.status.set(('측정 종료 · 기록 검사 완료' if manifest['run_status']=='succeeded' else '오류로 측정 종료') + f" · {manifest['recorded_frames']}프레임" + (' · '+self.last_error if manifest['run_status']=='failed' and self.last_error else ''))
                elif kind in ('error','command_rejected'):
                    self.pending = False
                    if kind == 'error':
                        self.posture.set('입력/분석 중단')
                        self.detection.set('입력/분석 중단')
                        self.had_error = True
                        self.last_error = value['message']
                        self.measure_status.set('오류 · 연결 종료 중')
                        self.frame = None
                        self.drag_start = self.drag_end = None
                        self.summary.set('입력/분석 중단 · 마지막 수신 결과는 현재 관측이 아닙니다.')
                        self.canvas.delete('all')
                        self.canvas.create_text(20,20,anchor='nw',text='입력/분석 중단 · 이전 결과를 현재 결과로 표시하지 않습니다.',fill='white',font=(self.font_family,12))
                    else:
                        self.measure_status.set('측정 중' if self.recording else '기록 대기')
                    self.status.set(('오류: ' if kind=='error' else '요청 확인: ') + value['message'])
                elif kind == 'playback':
                    self.playback_paused = value['paused']
                    self.play_button.configure(text='재생' if self.playback_paused else '일시정지')
                elif kind == 'eof':
                    self.ended = True
                    self.status.set('영상 끝 · 마지막 분석 프레임입니다. 다시 보려면 파일을 새로 여세요.')
                    self.detection.set('영상 끝')
                elif kind == 'closed':
                    if not self.had_error:
                        self.status.set(('영상 끝' if self.ended else '입력 연결 종료')+' · 마지막 결과입니다. 다시 분석하려면 새 입력으로 여세요.')
                    self.closed = True
                    self.recording = self.pending = False
        except queue.Empty:
            pass
        try:
            frame = self.worker.frames.get_nowait()
            if self.had_error:
                raise queue.Empty
            self.frame = frame
            self.geometry = (self.frame['rgb'].shape[1], self.frame['rgb'].shape[0])
            row = self.frame['row']
            count = row['candidate_count']
            candidate = '후보 없음' if count==0 else f'출력 후보 {count}개'
            if self.recording:
                self.record_count = self.frame['recorded_frames']
                if self.measure_started_ns is not None and int(row['received_ns']) >= self.measure_started_ns:
                    self.measure_elapsed = (int(row['received_ns'])-self.measure_started_ns)/1e9
            self.show_observation(row)
            if self.closed:
                self.detection.set(self.detection.get()+' · 마지막 결과')
            media = row.get('source_media')
            media_text = f" · 영상 {int(media['relative_time_ns'])/1e9:.2f}초" if media else ''
            self.summary.set(f"입력 프레임 {row['stream_frame_id']} · {candidate} · 최근 측정 기록 {self.record_count}개 · 측정 {self.measure_elapsed:.1f}초{media_text}")
            try:
                self.draw()
            except Exception as exc:
                self.had_error = True
                self.last_error = str(exc)
                self.frame = None
                self.canvas.delete('all')
                self.status.set('화면 표시 오류: '+str(exc))
                self.worker.request_close()
        except queue.Empty:
            pass
        self.update_controls()
        if self.closed:
            if self.closing and self.owns_window:
                self.root.destroy()
            return
        if self.closing and self.close_started is not None and time.monotonic()-self.close_started > 5 and not self.closed:
            self.status.set('장치 응답을 기다리는 중입니다. 강제 종료하면 기록이 미완료 상태로 남을 수 있습니다.')
        self.root.after(30, self.poll)

    def quit(self):
        if self.closing:
            return
        if self.closed and self.owns_window:
            self.root.destroy()
            return
        self.closing = True
        self.close_started = time.monotonic()
        self.status.set('입력을 종료하고 기록을 정리하고 있습니다.')
        self.worker.request_close()
        self.update_controls()


def launch_live(model=None, camera=None, output_root=Path('recordings/live'), demo=False, video=None,**options):
    from .live_web import launch_browser
    return launch_browser(model, camera, output_root, demo, video,**options)
