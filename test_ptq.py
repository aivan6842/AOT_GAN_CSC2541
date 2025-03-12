from AOT_GAN.src.model.aotgan import InpaintGenerator, AOTBlock
import torch
from attrdict import AttrDict
import numpy as np
from torchvision.transforms import ToTensor
import os
from tqdm import tqdm
from PIL import Image
import copy
from torch.ao.quantization.qconfig_mapping import get_default_qconfig_mapping, QConfigMapping, QConfig, get_default_qconfig
from torch.ao.quantization.quantize_fx import prepare_fx, convert_fx
from torch.ao.quantization.observer import HistogramObserver, PerChannelMinMaxObserver, MovingAverageMinMaxObserver

device = torch.device("cpu")
half_size_args = AttrDict({"block_num": 8, "rates": [1, 2, 4, 8]})

pct = "1"
# save_dir = f"/w/nobackup/385/scratch-space/expires-2024-Dec-23/aivan6842/test/ours/ood/{pct}"
save_dir = "tests/quant"
test_data_path = "data/x-medium/test"
# test_data_path = "/scratch/expires-2024-Dec-23/aivan6842/data/ood3/ood"
test_data_path = "tests/paper"
# masks_data_path = f"data/masks_{pct}"
masks_data_path = "tests/paper"
student_final_model = "AOT_GAN/experiments/places2/G0000000.pt"
# student_final_model = "/w/nobackup/385/scratch-space/expires-2024-Dec-23/aivan6842/models/student_generator_up_to_60_percent_mask_45.pt"

#### load model #####
quantized_model_path = "/w/340/aivan6842/csc2541/AOT_GAN_CSC2541/AOT_GAN/experiments/places2/generator_quantized.pth"

student_generator = InpaintGenerator(half_size_args).to(device)
student_generator.load_state_dict(torch.load(student_final_model, map_location=device, weights_only=True))
student_generator.eval()

model_to_quantize = copy.deepcopy(student_generator)
example_inputs = torch.rand(size=(1,3,512,512))


global_qconfig = get_default_qconfig()
# qconfig_map = QConfigMapping().set_global(global_qconfig)
qconfig_map = QConfigMapping()

for name, module in model_to_quantize.named_modules():
    if "encoder" in name:
        qconfig = QConfig(
                    activation=HistogramObserver.with_args(reduce_range=True),
                    weight=PerChannelMinMaxObserver.with_args(
                        qscheme=torch.per_channel_symmetric
                    ),
                )
        qconfig_map.set_module_name(name, get_default_qconfig())

prepared_model = prepare_fx(model_to_quantize, qconfig_map, example_inputs)

loaded_quantized_model = convert_fx(prepared_model)
loaded_quantized_model.load_state_dict(torch.load(quantized_model_path, weights_only=True))

for name, module in loaded_quantized_model.named_modules():
    print(name) 
import pdb; pdb.set_trace()


image_paths = ["beach_00004089.jpg", "valley_00003311.jpg", "valley_00000409.jpg", "beach_00000780.jpg"]
masks = ["02055.png", "05148.png", "06518.png", "04259.png"]

def postprocess(image):
    image = torch.clamp(image, -1.0, 1.0)
    image = (image + 1) / 2.0 * 255.0
    image = image.permute(1, 2, 0)
    image = image.cpu().numpy().astype(np.uint8)
    return Image.fromarray(image)

for image_path, mask_path in tqdm(zip(image_paths, masks), total=len(image_paths)):
    image = ToTensor()(Image.open(f"{test_data_path}/{image_path}").convert("RGB"))
    image = (image * 2.0 - 1.0).unsqueeze(0)
    mask = ToTensor()(Image.open(f"{masks_data_path}/{mask_path}").convert("L"))
    mask = mask.unsqueeze(0)
    image, mask = image.to(device), mask.to(device)
    image_masked = image * (1 - mask.float()) + mask

    with torch.no_grad():
        pred_img, _ = loaded_quantized_model(image_masked, mask)

    comp_imgs = (1 - mask) * image + mask * pred_img
    image_name = os.path.basename(image_path).split(".")[0]
    # postprocess(image_masked[0]).save(f"tests/{pct}_base/{image_name}_masked.png")
    # postprocess(pred_img[0]).save(f"tests/{pct}_base/{image_name}_pred.png")
    postprocess(comp_imgs[0]).save(f"{save_dir}/{image_name}_comp_aot.png")
