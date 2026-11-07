"""Optional YOLOS-tiny person boxes. Local inference never downloads weights."""
from pathlib import Path
import shutil
import tempfile
import re
from .io_utils import load_json, atomic_json, file_hash

MODEL_ID='hustvl/yolos-tiny'
DIRECTORY=Path('checkpoints/person-yolos-tiny')
MANIFEST='bedpose_person.json'
CROP_POLICY={'version':'person-crop-policy-0.1','min_box_side_px':16,'boundary_margin_px':1,
             'association':'current_tracked_shoulder_and_hip_inside_unique_nonoverlapping_box',
             'validation':'unvalidated_initial_values'}


def validate_assets(m):
    if (not isinstance(m,dict) or set(m)!={'version','model_id','revision','files'}
            or m['version']!='person-assets-0.1' or m['model_id']!=MODEL_ID
            or m['revision'] is not None and not re.fullmatch('[0-9a-f]{40}',str(m['revision']))
            or not isinstance(m['files'],dict) or set(m['files']) not in ({'config.json','preprocessor_config.json','model.safetensors'},
                                       {'config.json','preprocessor_config.json','pytorch_model.bin'})
            or any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in m['files'].values())):
        raise ValueError('Invalid person detector assets')
    return m


def read_assets(directory,verify=False):
    directory=Path(directory);m=validate_assets(load_json(directory/MANIFEST))
    if any(not (directory/f).is_file() or (directory/f).is_symlink() for f in m['files']):raise ValueError('사람 검출 모델 파일이 없습니다.')
    if verify and any(file_hash(directory/f)!=h for f,h in m['files'].items()):raise ValueError('사람 검출 모델 파일이 변경됐습니다.')
    cfg=load_json(directory/'config.json')
    if cfg.get('model_type')!='yolos' or 'person' not in cfg.get('id2label',{}).values():raise ValueError('YOLOS person label required')
    return m


def setup(output,source=None):
    output=Path(output).expanduser().resolve()
    if output.exists():raise ValueError('새 사람 검출 모델 저장 폴더를 지정하세요.')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent,prefix='.person-') as temp:
        revision=None
        if source is None:
            from huggingface_hub import HfApi,snapshot_download
            info=HfApi().model_info(MODEL_ID);revision=info.sha
            names={f.rfilename for f in info.siblings}
            weight='model.safetensors' if 'model.safetensors' in names else 'pytorch_model.bin'
            source=Path(snapshot_download(MODEL_ID,revision=revision,allow_patterns=['config.json','preprocessor_config.json',weight]))
        source=Path(source);staging=Path(temp)/'prepared';staging.mkdir()
        weight='model.safetensors' if (source/'model.safetensors').is_file() else 'pytorch_model.bin'
        for name in ('config.json','preprocessor_config.json',weight):shutil.copyfile(source/name,staging/name)
        m={'version':'person-assets-0.1','model_id':MODEL_ID,'revision':revision,
           'files':{f.name:file_hash(f) for f in staging.iterdir()}}
        atomic_json(staging/MANIFEST,m);read_assets(staging,verify=True);staging.rename(output)
    return m


class PersonDetector:
    def __init__(self,directory=DIRECTORY,device='cpu'):
        self.directory=Path(directory);self.assets=read_assets(directory)
        self.profile={'version':'person-detector-0.1','model_id':MODEL_ID,'assets':self.assets,
                      'device':device,'crop_policy':dict(CROP_POLICY),'score_min':.7,'max_side':640,'validation':'experimental_not_bed_validated'}
        self.model=self.processor=None
        self.load_error=None

    def detect(self,rgb):
        import torch
        from PIL import Image
        from transformers import YolosImageProcessor,YolosForObjectDetection
        if self.load_error is not None:raise RuntimeError('Person detector initialization failed; reconnect input')
        if self.model is None:
            try:
                if read_assets(self.directory,verify=True)!=self.assets:raise ValueError('Person detector assets changed')
                self.processor=YolosImageProcessor.from_pretrained(self.directory,local_files_only=True)
                self.model=YolosForObjectDetection.from_pretrained(self.directory,local_files_only=True,weights_only=True).to(self.profile['device']).eval()
            except Exception as exc:
                self.load_error=type(exc).__name__
                raise
        h,w=rgb.shape[:2];image=Image.fromarray(rgb);scale=min(1.,self.profile['max_side']/max(w,h))
        image=image.resize((max(16,round(w*scale)),max(16,round(h*scale))))
        inputs=self.processor(images=image,do_resize=False,return_tensors='pt')
        with torch.inference_mode():
            output=self.model(**{k:v.to(self.profile['device']) for k,v in inputs.items()})
            result=self.processor.post_process_object_detection(output,threshold=self.profile['score_min'],target_sizes=[(h,w)])[0]
        return [{'bounds':[float(x) for x in box],'score':float(score)}
                for box,score,label in zip(result['boxes'].cpu().tolist(),result['scores'].cpu().tolist(),result['labels'].cpu().tolist())
                if self.model.config.id2label[label]=='person']

    def close(self):self.model=self.processor=None


def validate_profile(p):
    if (not isinstance(p,dict) or set(p)!={'version','model_id','assets','device','crop_policy','score_min','max_side','validation'}
            or p['crop_policy']!=CROP_POLICY or p['version']!='person-detector-0.1' or p['model_id']!=MODEL_ID
            or p['device'] not in ('cpu','cuda','mps') or type(p['score_min']) not in (float,int) or p['score_min']!=.7
            or type(p['max_side']) is not int or p['max_side']!=640
            or p['validation']!='experimental_not_bed_validated'):raise ValueError('Unsupported person detector profile')
    validate_assets(p['assets'])
