#!/usr/bin/env python
# coding: utf-8

# In[ ]:


from AOT_GAN.src.model.aotgan import InpaintGenerator, Discriminator
from AOT_GAN.src.loss.loss import L1, Style, Perceptual, smgan
import torch
from collections import namedtuple
from attrdict import AttrDict
import numpy as np
import cv2
from torchvision.transforms import ToTensor
import os
from tqdm import tqdm

import torchvision.transforms as transforms
import torchvision.transforms.functional as F
from PIL import Image
from torch.utils.data import Dataset, DataLoader
from torch.utils.tensorboard import SummaryWriter

from torch import nn
from AOT_GAN.src.model.common import BaseNetwork
from AOT_GAN.src.model.aotgan import spectral_norm
from AOT_GAN.src.metric.metric import mae, psnr, ssim, fid
import random


# In[ ]:


device = torch.device("cuda")


# In[ ]:


# # # Model and version

# args_tuple = namedtuple("args", ["block_num", "rates"])
# args = args_tuple(block_num=8, rates=[1, 2, 4, 8])
# model = InpaintGenerator(args).to(device)
# model.load_state_dict(torch.load("/home/alex/Desktop/csc2541/AOT_GAN/experiments/places2/G0000000.pt", map_location=device))
# model.eval()

# sum(p.numel() for p in model.parameters() if p.requires_grad)


# In[ ]:


# mask = np.zeros((512, 512, 1), np.uint8)
# mask[:100, :, :] = 255
# filename = "AOT_GAN/my_examples/farmland.jpg"
# orig_img = cv2.resize(cv2.imread(filename, cv2.IMREAD_COLOR), (512, 512))


# In[ ]:


# def postprocess(image):
#     image = torch.clamp(image, -1.0, 1.0)
#     image = (image + 1) / 2.0 * 255.0
#     image = image.permute(1, 2, 0)
#     image = image.cpu().numpy().astype(np.uint8)
#     return image


# In[ ]:


# with torch.no_grad():
#     img_tensor = (ToTensor()(orig_img) * 2.0 - 1.0).unsqueeze(0).to(device)
#     mask_tensor = (ToTensor()(mask)).unsqueeze(0).to(device)
#     input_img = ((img_tensor * (1 - mask_tensor).float()) + mask_tensor).to(device)
#     pred_tensor, x_mid = model(input_img, mask_tensor)
#     comp_tensor = pred_tensor * mask_tensor + img_tensor * (1 - mask_tensor)

#     pred_np = postprocess(pred_tensor[0])
#     masked_np = postprocess(input_img[0])
#     comp_np = postprocess(comp_tensor[0])

#     cv2.imwrite("p.jpg", comp_np)


# # Paths

# In[16]:


# train_data_path = "/scratch/expires-2024-Nov-17/aivan6842/data/small/train"
# val_data_path = "/scratch/expires-2024-Nov-17/aivan6842/data/small/val"
# test_data_path = "/scratch/expires-2024-Nov-17/aivan6842/data/small/test"

train_data_path = "data/x-medium/train"
val_data_path = "data/x-medium/val"
test_data_path = "data/x-medium/test"

BATCH_SIZE = 16

teacher_model_path = "AOT_GAN/experiments/places2/G0000000.pt"

# # Data

# ## Dataset

# In[ ]:


class InpaintingData(Dataset):
    def __init__(self, root_dir: str, masks_dir: str = "data/masks"):
        super(Dataset, self).__init__()
        # images 
        self.images = os.listdir(f"{root_dir}")
        self.root_dir = root_dir
        self.masks_dir = masks_dir
        self.masks = os.listdir(masks_dir)
        random.seed(10)

        # augmentation
        self.img_trans = transforms.Compose(
            [
                transforms.RandomResizedCrop(512),
                transforms.RandomHorizontalFlip(),
                transforms.ColorJitter(0.05, 0.05, 0.05, 0.05),
                transforms.ToTensor(),
            ]
        )
        self.mask_trans = transforms.Compose(
            [
                transforms.Resize(512, interpolation=transforms.InterpolationMode.NEAREST),
                transforms.RandomHorizontalFlip(),
                transforms.RandomRotation((0, 45), interpolation=transforms.InterpolationMode.NEAREST),
            ]
        )

    def __len__(self):
        return len(self.images)

    def __getitem__(self, index):
        # load image
        image_path = os.path.join(f"{self.root_dir}", self.images[index])
        image = Image.open(image_path).convert("RGB")

        # get mask
        random_idx = random.randint(0, len(self.masks)-1)
        mask_path = os.path.join(f"{self.masks_dir}", self.masks[random_idx])
        mask = Image.open(mask_path).convert("L")

        # augment
        image = self.img_trans(image) * 2.0 - 1.0
        mask = F.to_tensor(self.mask_trans(mask))

        return image, mask, image_path


# In[ ]:


train = InpaintingData(train_data_path)
# val = InpaintingData(val_data_path)
test = InpaintingData(test_data_path)


# In[ ]:


print(len(train), len(test))


# ## Dataloaders

# In[ ]:


train_loader = DataLoader(train, batch_size=BATCH_SIZE, shuffle=True)
# val_loader = DataLoader(val, batch_size=BATCH_SIZE, shuffle=True)
test_loader = DataLoader(test, batch_size=BATCH_SIZE, shuffle=True)


# # Models

# In[ ]:


# class Discriminator(BaseNetwork):
#     def __init__(
#         self,
#     ):
#         super(Discriminator, self).__init__()
#         inc = 3
#         self.conv = nn.Sequential(
#             spectral_norm(nn.Conv2d(inc, 128, 4, stride=2, padding=1, bias=False)),
#             nn.LeakyReLU(0.2, inplace=True),
#             spectral_norm(nn.Conv2d(128, 512, 4, stride=1, padding=1, bias=False)),
#             nn.LeakyReLU(0.2, inplace=True),
#             nn.Conv2d(512, 1, 4, stride=1, padding=1),
#         )

#         self.init_weights()

#     def forward(self, x):
#         feat = self.conv(x)
#         return feat


# # Training

# In[ ]:


def train(run_name, 
          student_generator,
          teacher_generator,
          discriminator,
          L1_loss_weight=0.1,
          style_loss_weight=250,
          perceptual_loss_weight=0.1,
          adversarial_loss_weight=0.01,
          distillation_loss_weight=0.1,
          focused_loss_weight=0.2,
          num_epochs = 5,
          gen_lr = 1e-4,
          disc_lr = 1e-4,
          a=0.5,
          b=0.999,
          save_every=3,
          save_dir="models/",
          log_dir="./runs"):
    writer = SummaryWriter(f"{log_dir}/{run_name}")
    iteration = 0

    if not os.path.exists(save_dir):
        os.mkdir(save_dir)

    # Create losses
    L1_loss = L1()
    style_loss = Style()
    percetual_loss = Perceptual()
    adversarial_loss = smgan()
    distillation_loss = torch.nn.MSELoss()
    focused_loss = torch.nn.MSELoss()

    # get optimizers
    optimG = torch.optim.AdamW(student_generator.parameters(), lr=gen_lr, betas=(a, b))
    optimD = torch.optim.AdamW(discriminator.parameters(), lr=disc_lr, betas=(a, b))

    print("Beginning Training")

    for epoch in range(num_epochs):
        print(f"Epoch: {epoch}")

        for i, data in enumerate(tqdm(train_loader)):
            # get batch of data
            images, masks, _ = data
            images, masks = images.to(device), masks.to(device)
            masked_images = (images * (1 - masks).float()) + masks

            predicted_images, student_mids = student_generator(masked_images, masks)
            with torch.no_grad():
                teacher_predicted_images, teacher_mids = teacher_generator(masked_images, masks)
            inpainted_images = (1 - masks) * images + masks * predicted_images

            # losses
            l1_loss_val = L1_loss(predicted_images, images)
            focused_loss_val = focused_loss(predicted_images * masks, images * masks)
            style_loss_val = style_loss(predicted_images, images)
            percetual_loss_val = percetual_loss(predicted_images, images)
            distillation_loss_val = distillation_loss(student_mids, teacher_mids[1::2])
            adversarial_disc_loss, adversarial_gen_loss = adversarial_loss(discriminator, inpainted_images, images, masks)

            total_loss = (L1_loss_weight * l1_loss_val) + \
                         (style_loss_weight * style_loss_val) + \
                         (perceptual_loss_weight * percetual_loss_val) + \
                         (distillation_loss_weight * distillation_loss_val) + \
                         (focused_loss_weight * focused_loss_val) + \
                         (adversarial_loss_weight * adversarial_gen_loss)
        
            optimG.zero_grad()
            optimD.zero_grad()
            total_loss.backward()
            adversarial_disc_loss.backward()
            optimG.step()
            optimD.step()

            writer.add_scalar("Loss/train/generator", adversarial_gen_loss, iteration)
            writer.add_scalar("Loss/train/L1_loss", l1_loss_val, iteration)
            writer.add_scalar("Loss/train/style_loss", style_loss_val, iteration)
            writer.add_scalar("Loss/train/distillation_loss_val", distillation_loss_val, iteration)
            writer.add_scalar("Loss/train/perceptual_loss", percetual_loss_val, iteration)
            writer.add_scalar("Loss/train/focused_loss", focused_loss_val, iteration)
            writer.add_scalar("Loss/train/discriminator", adversarial_disc_loss, iteration)
            writer.add_scalar("Loss/train/total", total_loss, iteration)

            iteration += 1
        
        if (epoch + 1) % save_every == 0:
            torch.save(student_generator.state_dict(), os.path.join(save_dir, f"student_generator_{run_name}_{epoch}.pt"))
            torch.save(discriminator.state_dict(), os.path.join(save_dir, f"discriminator_{run_name}_{epoch}.pt"))
    
    # save final models
    torch.save(student_generator.state_dict(), os.path.join(save_dir, f"student_generator_{run_name}_final.pt"))
    torch.save(discriminator.state_dict(), os.path.join(save_dir, f"discriminator_{run_name}_final.pt"))


# In[ ]:


# create models
teacher_model_args = AttrDict({"block_num":8, "rates":[1, 2, 4, 8]})
teacher_model = InpaintGenerator(teacher_model_args).to(device)
teacher_model.load_state_dict(torch.load(teacher_model_path, map_location=device, weights_only=True))
teacher_model.eval()

half_size_args = AttrDict({"block_num": 4, "rates": [1, 2, 4, 8]})
student_model = InpaintGenerator(half_size_args).to(device)


# copy teacher encoder params into encoder
with torch.no_grad():
    student_model.encoder = teacher_model.encoder
    student_model.decoder = teacher_model.decoder

for param in student_model.encoder.parameters():
    param.requires_grad = False

for param in student_model.decoder.parameters():
    param.requires_grad = False


disc = Discriminator().to(device)

train(run_name="up_to_60_percent_mask",
      num_epochs=50,
      student_generator=student_model,
      teacher_generator=teacher_model,
      discriminator=disc,
      save_every=2,
      distillation_loss_weight=0.0001)