import argparse
import cv2
import numpy as np
import os
import torch
import torch.nn.functional as F
from torchvision.transforms import Compose
from tqdm import tqdm

from depth_anything.dpt import DPT_DINOv2
from depth_anything.util.transform import Resize, NormalizeImage, PrepareForNet


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--img-path', type=str)
    parser.add_argument('--outdir', type=str, default='./vis_depth')
    
    parser.add_argument('--encoder', type=str, default='vitl')
    parser.add_argument('--load-from', type=str, required=True)
    parser.add_argument('--localhub', dest='localhub', action='store_true', default=False)
    
    args = parser.parse_args()
    
    margin_width = 50
    caption_height = 60
    
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1
    font_thickness = 2
    
    assert args.encoder in ['vits', 'vitb', 'vitl']
    if args.encoder == 'vits':
        depth_anything = DPT_DINOv2(encoder='vits', features=64, out_channels=[48, 96, 192, 384], localhub=args.localhub).cuda()
    elif args.encoder == 'vitb':
        depth_anything = DPT_DINOv2(encoder='vitb', features=128, out_channels=[96, 192, 384, 768], localhub=args.localhub).cuda()
    else:
        depth_anything = DPT_DINOv2(encoder='vitl', features=256, out_channels=[256, 512, 1024, 1024], localhub=args.localhub).cuda()
    
    total_params = sum(param.numel() for param in depth_anything.parameters())
    print('Total parameters: {:.2f}M'.format(total_params / 1e6))
    
    depth_anything.load_state_dict(torch.load(args.load_from, map_location='cpu'), strict=True)
    
    depth_anything.eval()
    
    transform = Compose([
        Resize(
            width=518,
            height=518,
            resize_target=False,
            keep_aspect_ratio=True,
            ensure_multiple_of=14,
            resize_method='lower_bound',
            image_interpolation_method=cv2.INTER_CUBIC,
        ),
        NormalizeImage(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        PrepareForNet(),
    ])
    
    if os.path.isfile(args.img_path):
        if args.img_path.endswith('txt'):
            with open(args.img_path, 'r') as f:
                filenames = f.read().splitlines()
                filenames = [os.path.join(args.img_path, filename) for filename in filenames]
        else:
            filenames = [args.img_path]
    else:
        filenames = os.listdir(args.img_path)
        filenames.sort()
    
    for filename in tqdm(filenames):
        raw_image = cv2.imread(filename)
        image = cv2.cvtColor(raw_image, cv2.COLOR_BGR2RGB) / 255.0
        
        h, w = image.shape[:2]
        
        image = transform({'image': image})['image']
        image = torch.from_numpy(image).unsqueeze(0).cuda()
        
        with torch.no_grad():
            depth = depth_anything(image)
        
        depth = F.interpolate(depth[None], (h, w), mode='bilinear', align_corners=False)[0, 0]
        depth = (depth - depth.min()) / (depth.max() - depth.min()) * 255.0
        
        depth = depth.cpu().numpy().astype(np.uint8)
        depth_color = cv2.applyColorMap(depth, cv2.COLORMAP_INFERNO)
        
        os.makedirs(args.outdir, exist_ok=True)
        filename = os.path.basename(filename)
        
        split_region = np.ones((raw_image.shape[0], margin_width, 3), dtype=np.uint8) * 255
        combined_results = cv2.hconcat([raw_image, split_region, depth_color])
        
        caption_space = np.ones((caption_height, combined_results.shape[1], 3), dtype=np.uint8) * 255
        captions = ['Raw image', 'Depth Anything']
        segment_width = w + margin_width
        for i, caption in enumerate(captions):
            # Calculate text size
            text_size = cv2.getTextSize(caption, font, font_scale, font_thickness)[0]

            # Calculate x-coordinate to center the text
            text_x = int((segment_width * i) + (w - text_size[0]) / 2)

            # Add text caption
            cv2.putText(caption_space, caption, (text_x, 40), font, font_scale, (0, 0, 0), font_thickness)
        
        final_result = cv2.vconcat([caption_space, combined_results])
        
        cv2.imwrite(os.path.join(args.outdir, filename[:filename.find('.')] + '_img_depth.png'), final_result)
        