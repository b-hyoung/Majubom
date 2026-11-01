"""Input workspace: independent source/SDK/ROI/recording per tab; no sensor fusion."""
from pathlib import Path
import queue
import tempfile
import threading
import time
from .io_utils import atomic_json, file_hash
from .live_engine import LiveEngine, LiveWorker
from .live_gui import LiveWindow
from .live_sources import CameraSource, SyntheticSource, VideoFileSource, probe_cameras


class MeasurementWorkspace:
    def __init__(self, root, model=None, output_root=Path('recordings/live')):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        self.root, self.tk, self.ttk = root, tk, ttk
        self.filedialog, self.messagebox = filedialog, messagebox
        self.output_root = Path(output_root)
        self.temp = tempfile.TemporaryDirectory(prefix='bed-pose-inputs-')
        self.panels, self.probe_messages = [], queue.Queue()
        self.scanning, self.closing = False, False
        root.title('BedPose · 측정 작업실')
        width, height = root.winfo_screenwidth(),root.winfo_screenheight()
        root.geometry(f'{min(1280,width-70)}x{min(900,height-70)}')
        root.minsize(min(1040,width-70),min(720,height-70))
        root.configure(background='#edf2f7')
        header = ttk.Frame(root,padding=(20,14))
        header.pack(fill='x')
        ttk.Label(header,text='BedPose',font=('TkDefaultFont',22,'bold')).pack(side='left')
        ttk.Label(header,text='측정 작업실  /  카메라와 영상',font=('TkDefaultFont',11)).pack(side='left',padx=18)
        config = ttk.Frame(root,padding=(20,0,20,12))
        config.pack(fill='x')
        ttk.Label(config,text='추론 모델').grid(row=0,column=0,padx=(0,10))
        self.model = tk.StringVar(value=str(model or Path('checkpoints/pose_landmarker_full.task')))
        ttk.Entry(config,textvariable=self.model,width=58).grid(row=0,column=1,sticky='ew')
        ttk.Button(config,text='모델 선택…',command=self.choose_model).grid(row=0,column=2,padx=8)
        config.columnconfigure(1,weight=1)
        bar = ttk.Frame(root,padding=(20,0,20,10))
        bar.pack(fill='x')
        ttk.Label(bar,text='카메라 번호').pack(side='left')
        self.camera = tk.StringVar(value='0')
        self.camera_box = ttk.Combobox(bar,textvariable=self.camera,values=tuple(str(i) for i in range(6)),width=5)
        self.camera_box.pack(side='left',padx=8)
        self.scan_button = ttk.Button(bar,text='장치 확인',command=self.scan)
        self.scan_button.pack(side='left',padx=(0,8))
        self.add_button = ttk.Button(bar,text='카메라 연결',command=self.add_camera)
        self.add_button.pack(side='left',padx=(0,16))
        ttk.Button(bar,text='영상 파일 열기…',command=self.open_file).pack(side='left')
        ttk.Button(bar,text='선택 입력 제거',command=self.remove_selected).pack(side='right')
        self.notice = tk.StringVar(value='카메라 번호를 선택하거나 영상을 여세요. 여러 입력은 탭별로 독립 실행됩니다.')
        ttk.Label(root,textvariable=self.notice,wraplength=1150).pack(anchor='w',padx=20,pady=(0,8))
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill='both',expand=True,padx=10)
        self.welcome = ttk.Frame(self.tabs,padding=40)
        self.tabs.add(self.welcome,text='시작하기')
        ttk.Label(self.welcome,text='어떤 영상으로 측정할까요?',font=('TkDefaultFont',24,'bold')).pack(anchor='w',pady=(30,18))
        ttk.Label(self.welcome,text='카메라 연결   내장 · USB · PC에서 웹캠으로 인식되는 휴대폰\n\n영상 파일 열기   촬영해 둔 영상을 관절 추론과 함께 재생\n\n입력을 연 뒤 침대 영역을 드래그하고 측정을 시작하세요.',font=('TkDefaultFont',13),wraplength=850).pack(anchor='w')
        ttk.Label(root,text='입력마다 영역·분석·기록이 분리됩니다. 여러 카메라의 동기화·결과 융합은 제공하지 않습니다.',wraplength=1150).pack(anchor='w',padx=20,pady=10)
        root.protocol('WM_DELETE_WINDOW',self.quit)
        root.after(60,self.poll)

    def choose_model(self):
        path = self.filedialog.askopenfilename(title='MediaPipe Pose Landmarker 모델',filetypes=[('MediaPipe 모델','*.task'),('모든 파일','*.*')])
        if path:
            self.model.set(path)

    def new_engine(self, source, demo=False):
        if demo:
            from .pose.fake import FakeAdapter
            return LiveEngine(source,FakeAdapter(),{'execution_mode':'ordered_sequence'},self.output_root,execution_kind='synthetic')
        from .pose.mediapipe import MediaPipeAdapter, DEFAULT_OPTIONS, mapping_profile
        model = Path(self.model.get()).expanduser().resolve()
        if not model.is_file():
            raise ValueError('추론 모델(.task)을 먼저 선택하세요.')
        options = Path(self.temp.name)/'adapter_options.json'
        atomic_json(options,DEFAULT_OPTIONS)
        settings = {'execution_mode':'ordered_sequence','requested_device':'cpu',
                    'assets':[{'role':'pose_landmarker_task','path':str(model),'sha256':file_hash(model)}],
                    'adapter_options_path':str(options),'adapter_options_sha256':file_hash(options)}
        return LiveEngine(source,MediaPipeAdapter(),settings,self.output_root,profile=mapping_profile())

    def add_source(self, source, title, demo=False):
        if self.closing:
            return
        try:
            engine = self.new_engine(source,demo)
            tab = self.ttk.Frame(self.tabs)
            worker = LiveWorker(engine)
            panel = LiveWindow(self.root,worker,parent=tab,title=title)
            panel.tab, panel.remove_when_closed = tab, False
            if self.welcome is not None:
                self.tabs.forget(self.welcome)
                self.welcome.destroy()
                self.welcome = None
            self.tabs.add(tab,text=title)
            self.tabs.select(tab)
            self.panels.append(panel)
            worker.start()
            self.notice.set(f'입력 탭 {len(self.panels)}개 · 탭에서 연결 상태와 영상을 확인하고 침대 영역을 지정하세요.')
            return panel
        except Exception as exc:
            self.messagebox.showerror('입력을 열 수 없습니다',str(exc),parent=self.root)

    def active_camera_indices(self):
        return {p.worker.engine.source.index for p in self.panels
                if isinstance(p.worker.engine.source,CameraSource) and not p.closed}

    def add_camera(self):
        if self.scanning:
            return
        try:
            index = int(self.camera.get())
            if index < 0:
                raise ValueError()
        except ValueError:
            self.notice.set('카메라 번호에 0 이상의 정수를 입력하세요.')
            return
        if index in self.active_camera_indices():
            self.notice.set('이 카메라는 이미 열려 있습니다. 기존 탭을 선택하거나 연결을 종료하세요.')
            return
        self.add_source(CameraSource(index),f'카메라 {index}')

    def open_file(self):
        path = self.filedialog.askopenfilename(title='촬영 영상 열기',filetypes=[('영상 파일','*.mp4 *.mov *.mkv *.avi *.webm'),('모든 파일','*.*')])
        if path:
            self.add_source(VideoFileSource(path),Path(path).name)

    def scan(self):
        if self.scanning or self.closing:
            return
        self.scanning = True
        self.scan_button.configure(state='disabled')
        self.add_button.configure(state='disabled')
        self.notice.set('카메라 0~5를 확인하고 있습니다. 사용 중인 입력은 건너뜁니다. 장치에 따라 시간이 걸릴 수 있습니다.')
        excluded = self.active_camera_indices()
        def run():
            try:
                self.probe_messages.put((probe_cameras(excluded=excluded),None))
            except Exception as exc:
                self.probe_messages.put(([],str(exc)))
        threading.Thread(target=run,daemon=True).start()

    def remove_selected(self):
        selected = self.tabs.select()
        for panel in self.panels:
            if str(panel.tab) == selected:
                panel.remove_when_closed = True
                panel.quit()
                break

    def poll(self):
        try:
            found,error = self.probe_messages.get_nowait()
            self.scanning = False
            self.scan_button.configure(state='normal')
            self.add_button.configure(state='normal')
            available = sorted(set(found)|self.active_camera_indices())
            self.camera_box.configure(values=tuple(str(i) for i in available))
            if found:
                self.camera.set(str(found[0]))
            self.notice.set('장치 확인 오류: '+error if error else ('확인된 번호: '+', '.join(map(str,available)) if available else '0~5에서 카메라가 확인되지 않았습니다.')+' · 다른 번호는 직접 입력할 수 있습니다.')
        except queue.Empty:
            pass
        for panel in list(self.panels):
            if panel.remove_when_closed and panel.closed:
                self.tabs.forget(panel.tab)
                panel.tab.destroy()
                self.panels.remove(panel)
        if self.closing and all(p.closed for p in self.panels):
            self.temp.cleanup()
            self.root.destroy()
            return
        self.root.after(60,self.poll)

    def quit(self):
        if not self.closing:
            self.closing = True
            self.notice.set('모든 입력을 종료하고 기록을 정리하고 있습니다.')
            for panel in self.panels:
                panel.quit()


def launch_workspace(model=None,camera=0,output_root=Path('recordings/live'),demo=False,video=None):
    try:
        import tkinter as tk
        import cv2
    except ImportError as exc:
        raise ValueError('Python Tk 구성요소와 pip install -e .[live] 설치가 필요합니다.') from exc
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise ValueError('데스크톱 화면과 Python Tk 설치를 확인하세요.') from exc
    app = MeasurementWorkspace(root,model,output_root)
    if demo:
        app.add_source(SyntheticSource(),'합성 테스트',demo=True)
    elif video is not None:
        app.add_source(VideoFileSource(video),Path(video).name)
    # Without --camera/--video, launch only the input picker. Never guess a device.
    elif camera is not None:
        app.camera.set(str(camera))
        app.add_camera()
    try:
        root.mainloop()
    finally:
        for panel in app.panels:
            panel.worker.request_close()
        deadline = time.monotonic()+5
        for panel in app.panels:
            panel.worker.join(timeout=max(0,deadline-time.monotonic()))
        if not any(p.worker.is_alive() for p in app.panels):
            app.temp.cleanup()
    return 3 if any(p.had_error for p in app.panels) else 0
