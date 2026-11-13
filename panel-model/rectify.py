"""Rectify a photo of the panel onto the source image it shows (SIFT + RANSAC homography).
Usage: rectify.py SOURCE.png PHOTO.jpg   (writes rect_photo_810.png, side_by_side.png, Hfull.npy in the working dir)"""
import sys, cv2, numpy as np
from PIL import Image, ImageOps
SRC, PHOTO = sys.argv[1], sys.argv[2]
src = np.asarray(Image.open(SRC).convert('RGB'))
photo = np.asarray(ImageOps.exif_transpose(Image.open(PHOTO)).convert('RGB'))
print('src', src.shape, 'photo', photo.shape)
# match at reduced scale
s_small = cv2.resize(src, (810,1080), interpolation=cv2.INTER_AREA)
p_scale = 0.35
p_small = cv2.resize(photo, None, fx=p_scale, fy=p_scale, interpolation=cv2.INTER_AREA)
sift = cv2.SIFT_create(nfeatures=8000)
g1 = cv2.cvtColor(s_small, cv2.COLOR_RGB2GRAY); g2 = cv2.cvtColor(p_small, cv2.COLOR_RGB2GRAY)
g1 = cv2.createCLAHE(3.0,(8,8)).apply(g1); g2 = cv2.createCLAHE(3.0,(8,8)).apply(g2)
k1,d1 = sift.detectAndCompute(g1,None); k2,d2 = sift.detectAndCompute(g2,None)
print('kp', len(k1), len(k2))
bf = cv2.BFMatcher(cv2.NORM_L2)
m = bf.knnMatch(d1,d2,k=2)
good = [a for a,b in m if a.distance < 0.75*b.distance]
print('good matches', len(good))
pts1 = np.float32([k1[x.queryIdx].pt for x in good]); pts2 = np.float32([k2[x.trainIdx].pt for x in good])
H, mask = cv2.findHomography(pts2, pts1, cv2.RANSAC, 4.0)
print('inliers', int(mask.sum()))
# Compose: photo_full -> photo_small (scale) -> src_small (H) ; want map photo_full -> src_small frame
S = np.array([[p_scale,0,0],[0,p_scale,0],[0,0,1]])
Hfull = H @ S
rect = cv2.warpPerspective(photo, Hfull, (810,1080), flags=cv2.INTER_AREA)
Image.fromarray(rect).save('rect_photo_810.png')
# where do the src corners land in the photo? (for sanity)
Hinv = np.linalg.inv(Hfull)
corners = np.float32([[0,0],[810,0],[810,1080],[0,1080]]).reshape(-1,1,2)
pc = cv2.perspectiveTransform(corners, Hinv).reshape(-1,2)
print('src corners in photo px:', pc.round(0).tolist())
side = np.concatenate([s_small, rect], axis=1)
Image.fromarray(side).resize((810,540)).save('side_by_side.png')
np.save('Hfull.npy', Hfull)
