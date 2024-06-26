import logging
import random

import torch
from torch.cuda.amp import autocast as autocast
import torch.nn as nn

from minigpt4.common.registry import registry
from minigpt4.models.base_model import disabled_train
from minigpt4.models.minigpt_base import MiniGPTBase
from minigpt4.models.Qformer import BertConfig, BertLMHeadModel


@registry.register_model("minigpt_v2")
class MiniGPTv2(MiniGPTBase):
    """
    MiniGPT-v2 model
    """

    PRETRAINED_MODEL_CONFIG_DICT = {
        "pretrain": "configs/models/minigpt_v2.yaml",
    }

    def __init__(
            self,
            vit_model="eva_clip_g",
            img_size=448,
            drop_path_rate=0,
            use_grad_checkpoint=False,
            vit_precision="fp16",
            freeze_vit=True,
            llama_model="",
            prompt_template='[INST] {} [/INST]',
            max_txt_len=300,
            end_sym='\n',
            lora_r=64,
            lora_target_modules=["q_proj", "v_proj"],
            lora_alpha=16,
            lora_dropout=0.05,
            chat_template=False,
            use_grad_checkpoint_llm=False,
            max_context_len=3800,
            low_resource=False,  # use 8 bit and put vit in cpu
            device_8bit=0,  # the device of 8bit model should be set when loading and cannot be changed anymore.
    ):
        super().__init__(
            vit_model=vit_model,
            img_size=img_size,
            drop_path_rate=drop_path_rate,
            use_grad_checkpoint=use_grad_checkpoint,
            vit_precision=vit_precision,
            freeze_vit=freeze_vit,
            llama_model=llama_model,
            max_txt_len=max_txt_len,
            max_context_len=max_context_len,
            end_sym=end_sym,
            prompt_template=prompt_template,
            low_resource=low_resource,
            device_8bit=device_8bit,
            lora_r=lora_r,
            lora_target_modules=lora_target_modules,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
        )

        img_f_dim = self.visual_encoder.num_features * 4 ###CHANGED
        # img_f_dim = 3840 ###TO THIS
        
        self.mid_proj = [nn.Linear(1408, 128).to('cuda') for i in range(38)] ###ADDED was 64 => 128
        
        self.post_mid_proj = nn.Linear(128*38*4, img_f_dim).to('cuda') ###ADDED was 64 => 128
        
        self.llama_proj = nn.Linear(
            img_f_dim * 2, self.llama_model.config.hidden_size
        )
        # print()
        # print('img_f_dim ', img_f_dim)
        # print('self.llama_model.config.hidden_size ', self.llama_model.config.hidden_size)
        # print()
        self.chat_template = chat_template

        if use_grad_checkpoint_llm:
            self.llama_model.gradient_checkpointing_enable()

    def encode_img(self, image):
        device = image.device

        if len(image.shape) > 4:
            image = image.reshape(-1, *image.shape[-3:])

        with self.maybe_autocast():
            encoded_image = self.visual_encoder(image)
            # print(len(encoded_image))
            # image_embeds = self.ln_vision(encoded_image).to(device) ###CHANGED
            
            image_embeds = torch.stack([self.ln_vision(e).to(device) for e in encoded_image]) ###TO THIS
            # print(image_embeds.shape)
            # image_embeds = self.ln_vision(torch.stack(self.visual_encoder(image).hidden_states)).to(device) ###TO THIS
            image_embeds = image_embeds[:, :, 1:, :]
            
            mid_proj_outs = [self.mid_proj[i](h) for i, h in enumerate(image_embeds[:-1])] ###ADDED
                    
            mid_proj_stack = torch.stack(mid_proj_outs) ###ADDED
            
            ls_, bs_, pn_, hs_ = mid_proj_stack.shape ###ADDED
       
            mid_proj_embeds = mid_proj_stack.view(int(ls_ / 38) ,int(bs_), int(pn_ / 4), int(hs_ * 38 * 4)).squeeze() ###ADDED
            
            post_mid_proj_embeds = self.post_mid_proj(mid_proj_embeds)
            
            
            
            # bs, pn, hs = image_embeds.shape ###CHANGED
            bs, pn, hs = image_embeds[-1].shape ###TO THIS
            
            # image_embeds = image_embeds.view(bs, int(pn / 4), int(hs * 4)) ###CHANGED
            image_embeds = image_embeds[-1].view(bs, int(pn / 4), int(hs * 4)).squeeze() ###TO THIS
            
            # print('SHAPES OF LAST AND MID EMBEDS POST PROCESSING')
            # print(image_embeds.shape, post_mid_proj_embeds.shape)
            
            full_embeds = torch.cat([image_embeds, post_mid_proj_embeds], dim=-1)###ADDED
            # print(full_embeds.shape)
            # inputs_llama = self.llama_proj(image_embeds) ###CHANGED
            inputs_llama = self.llama_proj(full_embeds) ###TO THIS
            atts_llama = torch.ones(inputs_llama.size()[:-1], dtype=torch.long).to(image.device)
        return inputs_llama, atts_llama

    @classmethod
    def from_config(cls, cfg):
        vit_model = cfg.get("vit_model", "eva_clip_g")
        img_size = cfg.get("image_size")
        llama_model = cfg.get("llama_model")

        drop_path_rate = cfg.get("drop_path_rate", 0)
        use_grad_checkpoint = cfg.get("use_grad_checkpoint", False)
        vit_precision = cfg.get("vit_precision", "fp16")
        freeze_vit = cfg.get("freeze_vit", True)
        low_resource = cfg.get("low_resource", False)

        prompt_template = cfg.get("prompt_template", '[INST] {} [/INST]')
        max_txt_len = cfg.get("max_txt_len", 300)
        end_sym = cfg.get("end_sym", '\n')

        lora_r = cfg.get("lora_r", 64)
        lora_alpha = cfg.get("lora_alpha", 16)
        chat_template = cfg.get("chat_template", False)

        use_grad_checkpoint_llm = cfg.get("use_grad_checkpoint_llm", False)
        max_context_len = cfg.get("max_context_len", 3800)

        model = cls(
            vit_model=vit_model,
            img_size=img_size,
            drop_path_rate=drop_path_rate,
            use_grad_checkpoint=use_grad_checkpoint,
            vit_precision=vit_precision,
            freeze_vit=freeze_vit,
            llama_model=llama_model,
            prompt_template=prompt_template,
            max_txt_len=max_txt_len,
            low_resource=low_resource,
            end_sym=end_sym,
            lora_r=lora_r,
            lora_alpha=lora_alpha,
            chat_template=chat_template,
            use_grad_checkpoint_llm=use_grad_checkpoint_llm,
            max_context_len=max_context_len,
        )

        ckpt_path = cfg.get("ckpt", "")  # load weights of MiniGPT-4
        if ckpt_path:
            print("Load Minigpt-4-LLM Checkpoint: {}".format(ckpt_path))
            ckpt = torch.load(ckpt_path, map_location="cpu")
            msg = model.load_state_dict(ckpt['model'], strict=False)

        return model
