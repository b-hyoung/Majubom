"""Inverse quarter-turns for SDK coordinates; no anatomical left/right swapping."""
from copy import deepcopy


def restore_result(result,turns,width,height):
    turns%=4
    if not turns:return result
    result=deepcopy(result)
    for group in result.get('landmarks',[]):
        for p in group:
            x,y=p['x'],p['y']
            if turns==1:p['x'],p['y']=1-y,x
            elif turns==2:p['x'],p['y']=1-x,1-y
            else:p['x'],p['y']=y,1-x
            if p.get('z') is not None and turns%2:p['z']*=height/width
    for group in result.get('worldLandmarks',[]):
        for p in group:
            x,y=p['x'],p['y']
            if turns==1:p['x'],p['y']=-y,x
            elif turns==2:p['x'],p['y']=-x,-y
            else:p['x'],p['y']=y,-x
    return result
