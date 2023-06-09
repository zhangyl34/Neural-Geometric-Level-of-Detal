'''
octree signed distance field
'''
import math 
import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F

from lib.models.BaseLOD import BaseLOD
#from lib.models.BasicDecoder import BasicDecoder
from lib.utils import PerfTimer

class MyActivation(nn.Module):
    def forward(self, x):
        return torch.sin(x)

class FeatureVolume(nn.Module):
    def __init__(self, fdim, fsize):
        super().__init__()
        # 4, 8, 16, 32, 64 
        self.fsize = fsize
        # 32
        self.fdim = fdim
        # (1, 32, 5, 5, 5)
        self.fm = nn.Parameter(torch.randn(1, fdim, fsize+1, fsize+1, fsize+1) * 0.01)
        self.sparse = None

    def forward(self, x):
        # 三线性插值问询特征向量
        N = x.shape[0]
        if x.shape[1] == 3:
            sample_coords = x.reshape(1, N, 1, 1, 3)  # [N, 1, 1, 3]    
            sample = F.grid_sample(self.fm, sample_coords, 
                                   align_corners=True, padding_mode='border')[0,:,:,0,0].transpose(0,1)
        else:
            sample_coords = x.reshape(1, N, x.shape[1], 1, 3)  # [N, 1, 1, 3]    
            sample = F.grid_sample(self.fm, sample_coords, 
                                   align_corners=True, padding_mode='border')[0,:,:,:,0].permute([1,2,0])
        
        return sample

class OctreeSDF(BaseLOD):
    def __init__(self, args, init=None):
        super().__init__(args)
        # 32
        self.fdim = self.args.feature_dim
        # 4
        self.fsize = self.args.feature_size
        # 隐藏层维度 128
        self.hidden_dim = self.args.hidden_dim
        # False
        self.pos_invariant = self.args.pos_invariant

        self.features = nn.ModuleList([])
        # range(5)
        for i in range(self.args.num_lods):
            # 2**(i+2)
            self.features.append(FeatureVolume(self.fdim, (2**(i+self.args.base_lod))))
        # None
        self.interpolate = self.args.interpolate

        self.louts = nn.ModuleList([])

        self.sdf_input_dim = self.fdim
        # not False
        if not self.pos_invariant:
            # input 维度 32+3
            self.sdf_input_dim += self.input_dim
        # 5 个 decoder
        self.num_decoder = 1 if args.joint_decoder else self.args.num_lods 
        # range(5)
        for i in range(self.num_decoder):
            self.louts.append(
                nn.Sequential(
                    nn.Linear(self.sdf_input_dim, self.hidden_dim, bias=True),
                    nn.ReLU(),
                    nn.Linear(self.hidden_dim, 1, bias=True),
                )
            )
        
    def encode(self, x):
        # 禁用 encode 方法
        return x

    def sdf(self, x, lod=None, return_lst=False):
        # lod 0,1,2,3,4
        if lod is None:
            lod = self.lod
        
        # Query
        l = []
        samples = []
        # range(5)
        for i in range(self.num_lods):
            # 三线性插值问询特征向量
            sample = self.features[i](x)
            samples.append(sample)
            # 不同深度的特征向量累加
            if i > 0:
                samples[i] += samples[i-1]
            # 特征向量中加入 xyz 信息
            ex_sample = samples[i]
            # not False
            if not self.pos_invariant:
                ex_sample = torch.cat([x, ex_sample], dim=-1)
            # 5
            if self.num_decoder == 1:
                prev_decoder = self.louts[0]
                curr_decoder = self.louts[0]
            else:
                prev_decoder = self.louts[i-1]
                curr_decoder = self.louts[i]
            d = curr_decoder(ex_sample)
            # None
            if self.interpolate is not None and lod is not None:
                if i == len(self.louts) - 1:
                    return d
                if lod+1 == i:
                    _ex_sample = samples[i-1]
                    if not self.pos_invariant:
                        _ex_sample = torch.cat([x, _ex_sample], dim=-1)
                    _d = prev_decoder(_ex_sample)
                    return (1.0 - self.interpolate) * _l + self.interpolate * d
            else: 
                # d = curr_decoder(ex_sample)
                # train: 返回 d
                if lod is not None and lod == i:
                    return d
                l.append(d)

        if self.training:
            self.loss_preds = l

        if return_lst:
            return l
        else:
            return l[-1]
    
