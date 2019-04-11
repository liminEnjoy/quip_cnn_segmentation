import numpy as np
import os
import sys
import cv2
from PIL import Image
from scipy import ndimage
from skimage.morphology import watershed
from skimage.color import label2rgb

import detection_binarize
from gen_json import gen_meta_json

def apply_segmentation(in_path, image_id, wsi_width, wsi_height, method_description,
        seg_thres=0.33, det_thres=0.07, win_size=200, min_nucleus_size=20, max_nucleus_size=65536):

    def zero_padding(array, margin):
        size1, size2 = array.shape
        padded = np.zeros((size1+margin*2, size2+margin*2), dtype=array.dtype)
        padded[margin:-margin, margin:-margin] = array
        return padded

    def remove_padding(array, margin):
        return array[margin:-margin, margin:-margin]

    def seed_recall(seeds, seg_region, potential):
        labeled_region, n_region = ndimage.measurements.label(seg_region)
        for regi in range(1, n_region+1):
            bin_region = (labeled_region == regi)
            if (seeds * bin_region).sum() == 0 and bin_region.sum() < max_nucleus_size:
                region_potential = potential*bin_region
                maxx, maxy = np.unravel_index(np.argmax(region_potential, axis=None), region_potential.shape)
                seeds[maxx, maxy] = True
        return seeds

    def read_instance(im_file, resize_factor):
        det_seg = Image.open(im_file).convert('RGB')
        if resize_factor != 1:
            det_seg = det_seg.resize((det_seg.size[0]*resize_factor,
                det_seg.size[1]*resize_factor), resample=Image.NEAREST)
        det_seg = np.array(det_seg)

        return det_seg[..., 0], det_seg[..., 1]

    print "Watershed postprocessing on", in_path

    file_id = os.path.basename(in_path)[:-len('_SEG.png')]
    resize_factor = int(file_id.split('_')[5])
    detection, segmentation = read_instance(in_path, resize_factor)

    global_xy_offset = [int(x) for x in file_id.split('_')[0:2]]

    # Padding and smoothing
    padding_size = win_size + 10
    detection = zero_padding(detection_binarize.detection_peaks(detection, det_thres), padding_size)
    segmentation = zero_padding(segmentation, padding_size)
    segmentation = ndimage.filters.gaussian_filter(segmentation, 0.5, mode='mirror')

    seeds = seed_recall(detection>0, segmentation>(seg_thres*255), segmentation)

    markers = ndimage.measurements.label(ndimage.morphology.binary_dilation(seeds, np.ones((3,3))))[0]
    water_segmentation = watershed(-segmentation, markers,
            mask=(segmentation>(seg_thres*255)), compactness=1.0)

    xs, ys = np.where(seeds)
    fid = open(os.path.join(os.path.dirname(in_path), file_id+'-features.csv'), 'w')
    fid.write('AreaInPixels,PhysicalSize,Polygon\n')
    for nucleus_id, (x, y) in enumerate(zip(xs, ys)):
        seg_win = water_segmentation[x-win_size:x+win_size+1, y-win_size:y+win_size+1]
        bin_win = (seg_win == water_segmentation[x, y])

        # Fill holes in object
        bin_win = ndimage.binary_fill_holes(bin_win)
        physical_size = bin_win.sum()
        if physical_size < min_nucleus_size or physical_size >= bin_win.size:
            continue

        xoff = float(y - win_size - padding_size)
        yoff = float(x - win_size - padding_size)
        poly = cv2.findContours(bin_win.astype(np.uint8), cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE)[-2][0][:,0,:].astype(np.float32)
        poly[:, 0] = (poly[:, 0] + xoff) / resize_factor + global_xy_offset[0]
        poly[:, 1] = (poly[:, 1] + yoff) / resize_factor + global_xy_offset[1]
        poly_str = ':'.join(['{:.1f}'.format(x) for x in poly.flatten().tolist()])
        fid.write('{},{},[{}]\n'.format(
            int(physical_size/resize_factor/resize_factor), int(physical_size), poly_str))
    fid.close()

    gen_meta_json(in_path, image_id, wsi_width, wsi_height, method_description,
            seg_thres, det_thres, win_size, min_nucleus_size, max_nucleus_size)
