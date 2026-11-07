"""Image quadrilaterals and unit-square coordinates; no metric or 3D inference."""
from functools import lru_cache
import math
import numpy as np

METHOD='manual_quadrilateral'

def cross(a,b,c):return (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])

def make_quadrilateral(points,width,height):
    if any(type(v) is not int or v<=0 for v in (width,height)):raise ValueError('영상 크기가 올바르지 않습니다.')
    if (not isinstance(points,list) or len(points)!=4 or any(not isinstance(p,list) or len(p)!=2 or
          any(type(v) not in (int,float) or not math.isfinite(v) for v in p) for p in points)):
        raise ValueError('침대 모서리 네 점을 순서대로 지정하세요.')
    if any(not 0<=x<=width or not 0<=y<=height for x,y in points):raise ValueError('네 점은 영상 안에 있어야 합니다.')
    turns=[cross(points[i],points[(i+1)%4],points[(i+2)%4]) for i in range(4)]
    if not (all(t>0 for t in turns) or all(t<0 for t in turns)):
        raise ValueError('선이 교차하거나 안으로 꺾이지 않도록 모서리를 순서대로 찍으세요.')
    area=abs(sum(points[i][0]*points[(i+1)%4][1]-points[(i+1)%4][0]*points[i][1] for i in range(4)))/2
    if area<16 or any(math.dist(points[i],points[(i+1)%4])<2 for i in range(4)):
        raise ValueError('침대 영역이 너무 작습니다. 모서리 간격을 넓혀 주세요.')
    _homography(tuple(tuple(p) for p in points),width,height)
    xs,ys=zip(*points)
    return {'method':METHOD,'vertices':[list(p) for p in points],'width':width,'height':height,
            'x1':min(xs),'y1':min(ys),'x2':max(xs),'y2':max(ys),
            'normalized':[[x/width,y/height] for x,y in points]}

@lru_cache(maxsize=128)
def _homography(points,width,height):
    rows=[];rhs=[]
    for (x,y),(u,v) in zip(points,((0,0),(1,0),(1,1),(0,1))):
        x/=width;y/=height
        rows.extend(([x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]));rhs.extend((u,v))
    try:h=np.linalg.solve(np.array(rows,dtype=float),np.array(rhs,dtype=float))
    except np.linalg.LinAlgError as exc:raise ValueError('네 점의 좌표 변환이 불안정합니다.') from exc
    if not np.isfinite(h).all() or np.linalg.cond(np.array(rows))>1e12:raise ValueError('네 점의 좌표 변환이 불안정합니다.')
    return tuple(float(v) for v in h)

def is_quad(region):return isinstance(region,dict) and region.get('method')==METHOD

def rectangle(region):
    return tuple(region[k] for k in ('x1','y1','x2','y2')) if isinstance(region,dict) else region

def contains(region,point):
    if region is None:return False
    if not is_quad(region):
        x1,y1,x2,y2=rectangle(region);return x1<=point[0]<=x2 and y1<=point[1]<=y2
    pts=region['vertices'];values=[cross(pts[i],pts[(i+1)%4],point) for i in range(4)]
    eps=1e-9*max(region['width'],region['height'])**2
    return all(v>=-eps for v in values) or all(v<=eps for v in values)

def relative_xy(region,point):
    if region is None:return None
    if not is_quad(region):
        x1,y1,x2,y2=rectangle(region);return [(point[0]-x1)/(x2-x1),(point[1]-y1)/(y2-y1)]
    a,b,c,d,e,f,g,h=_homography(tuple(tuple(p) for p in region['vertices']),region['width'],region['height'])
    x,y=point[0]/region['width'],point[1]/region['height'];den=g*x+h*y+1
    if abs(den)<1e-12:return None
    uv=[(a*x+b*y+c)/den,(d*x+e*y+f)/den]
    return uv if all(math.isfinite(v) for v in uv) else None

def observation_region(region):
    return region if is_quad(region) else rectangle(region) if region else None
