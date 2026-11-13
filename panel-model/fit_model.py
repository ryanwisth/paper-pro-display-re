"""Fit model.json (tone map + per-hue chroma ratio, ceiling and hue shift) from a rectified panel photo.
Usage: python3 fit_model.py SOURCE.png   (run in the folder holding rect_photo_810.png from rectify.py)"""
import sys, cv2, numpy as np, json
from PIL import Image
src = np.asarray(Image.open(sys.argv[1]).convert('RGB'))
src = cv2.resize(src,(810,1080),interpolation=cv2.INTER_AREA)
rect = np.asarray(Image.open('rect_photo_810.png').convert('RGB')).astype(np.float32)
# --- white balance the photo: source near-neutral bright pixels should map to neutral in photo
srcf = src.astype(np.float32)
lab_s = cv2.cvtColor(src, cv2.COLOR_RGB2Lab).astype(np.float32)
sC = np.hypot(lab_s[...,1]-128, lab_s[...,2]-128); sL = lab_s[...,0]*100/255
neutral = (sC < 10) & (sL > 55)
print('neutral px used for WB:', int(neutral.sum()))
gains = srcf[neutral].mean(0) / rect[neutral].mean(0)
gains = gains / gains[1]   # anchor green
print('WB gains R,G,B =', gains.round(3))
wb = np.clip(rect*gains, 0, 255).astype(np.uint8)
Image.fromarray(wb).save('rect_photo_wb.png')
# --- measure in Lab after WB, blurred
k=7; m=25
sb = cv2.GaussianBlur(src,(0,0),k)[m:-m,m:-m]; rb = cv2.GaussianBlur(wb,(0,0),k)[m:-m,m:-m]
def lab(x):
    l = cv2.cvtColor(x, cv2.COLOR_RGB2Lab).astype(np.float32); return l[...,0]*100/255, l[...,1]-128, l[...,2]-128
sL,sa,sbb = lab(sb); rL,ra,rbb = lab(rb)
sC=np.hypot(sa,sbb); rC=np.hypot(ra,rbb); sH=(np.degrees(np.arctan2(sbb,sa)))%360; rH=(np.degrees(np.arctan2(rbb,ra)))%360
model={}
# tone: piecewise via binned medians (src L -> photo L), monotone smoothed
edges=np.linspace(0,100,21); xs=[]; ys=[]
for i in range(20):
    sel=(sL>=edges[i])&(sL<edges[i+1])
    if sel.sum()>150: xs.append(float((edges[i]+edges[i+1])/2)); ys.append(float(np.median(rL[sel])))
ys=np.maximum.accumulate(np.array(ys)); xs=np.array(xs)
model['tone_x']=xs.tolist(); model['tone_y']=ys.tolist()
A=np.vstack([np.ones(sL.size),sL.ravel()]).T; coef=np.linalg.lstsq(A,rL.ravel(),rcond=None)[0]
model['tone_linear_a_b']=[float(coef[0]),float(coef[1])]
# chroma: per hue band (24 bands, src hue), ratio at moderate chroma + ceiling (p98 of photo chroma in band)
nb=24; hb=np.linspace(0,360,nb+1); ratio=[]; ceil=[]; shift=[]; centers=[]
for i in range(nb):
    sel=(sH>=hb[i])&(sH<hb[i+1])&(sC>12)
    centers.append(float((hb[i]+hb[i+1])/2))
    if sel.sum()>200:
        # robust slope through origin on the lower-chroma half (where the panel isn't saturating)
        lo = sel & (sC < np.percentile(sC[sel],60))
        r = float(np.median(rC[lo]/sC[lo])) if lo.sum()>50 else float(np.median(rC[sel]/sC[sel]))
        ratio.append(r); ceil.append(float(np.percentile(rC[sel],98)))
        shift.append(float(np.median(((rH[sel]-sH[sel]+540)%360)-180)))
    else:
        ratio.append(np.nan); ceil.append(np.nan); shift.append(np.nan)
ratio=np.array(ratio); ceil=np.array(ceil); shift=np.array(shift)
# fill gaps by circular interpolation
def fill(v):
    v=v.copy(); idx=np.arange(len(v)); ok=~np.isnan(v)
    if ok.all(): return v
    v[~ok]=np.interp(idx[~ok], np.concatenate([idx[ok]-len(v), idx[ok], idx[ok]+len(v)]), np.concatenate([v[ok]]*3)); return v
ratio=fill(ratio); ceil=fill(ceil); shift=fill(shift)
# light circular smoothing
def smooth(v): return (np.roll(v,1)+2*v+np.roll(v,-1))/4
ratio=smooth(ratio); ceil=smooth(ceil); shift=smooth(shift)
model['hue_centers']=centers; model['chroma_ratio']=ratio.round(4).tolist(); model['chroma_ceiling']=ceil.round(2).tolist(); model['hue_shift_deg']=shift.round(1).tolist()
model['wb_gains']=gains.tolist()
model['notes']='Forward model of Gallery3 panel as photographed (iPhone, kitchen light, WB-corrected on source-neutral pixels). L: monotone binned map tone_x->tone_y. Chroma: C_out = min(ratio(h)*C, soft ceiling(h)). Hue: h_out = h + shift(h). Units: CIE Lab (L 0-100, a/b ~ +-128) via OpenCV.'
json.dump(model,open('model.json','w'),indent=1)
for c,r,ce,s in zip(centers,ratio,ceil,shift): print(f"hue {c:5.1f}: ratio {r:.3f} ceiling {ce:5.1f} shift {s:+6.1f}")
print('tone', [f"{x:.0f}->{y:.0f}" for x,y in zip(xs,ys)])
print('linear', coef.round(3))
