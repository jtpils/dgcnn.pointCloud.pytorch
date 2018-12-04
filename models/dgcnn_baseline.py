import os, sys
import time
import torch
import torch.nn as nn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, '../utils'))

import nn_utils

def get_edge_features(x, k):
    """
    Args:
        x: point cloud [B, dims, N]
        k: kNN neighbours
    Return:
        [B, 2dims, N, k]    
    """
    B, dims, N = x.shape

    # batched pair-wise distance
    xt = x.permute(0, 2, 1)
    xi = -2 * torch.bmm(xt, x)
    xs = torch.sum(xt**2, dim=2, keepdim=True)
    xst = xs.permute(0, 2, 1)
    dist = xi + xs + xst # [B, N, N]

    # get k NN id    
    _, idx = torch.sort(dist, dim=2)
    idx = idx[: ,: ,1:k+1] # [B, N, k]
    idx = idx.contiguous().view(B, N*k)

    # gather
    neighbors = []
    for b in range(B):
        tmp = torch.index_select(x[b], 1, idx[b]) # [d, N*k] <- [d, N], 0, [N*k]
        tmp = tmp.view(dims, N, k)
        neighbors.append(tmp)
    neighbors = torch.stack(neighbors) # [B, d, N, k]

    # centralize
    central = x.unsqueeze(3) # [B, d, N, 1]
    central = central.repeat(1, 1, 1, k) # [B, d, N, k]

    ee = torch.cat([central, neighbors-central], dim=1)
    assert ee.shape == (B, 2*dims, N, k)
    return ee

class edgeConv(nn.Module):
    """ Edge Convolution using 1x1 Conv h
    [B, Fin, N] -> [B, Fout, N]
    """
    def __init__(self, Fin, Fout, k):
        super(edgeConv, self).__init__()
        self.k = k
        self.Fin = Fin
        self.Fout = Fout
        self.conv = nn.Conv2d(2*Fin, Fout, 1)

    def forward(self, x):
        B, Fin, N = x.shape
        
        x = get_edge_features(x, self.k); # [B, 2Fin, N, k]
        
        x = self.conv(x) # [B, Fout, N, k]

        x, _ = torch.max(x, 3) # [B, Fout, N]

        assert x.shape == (B, self.Fout, N)
        return x


class dgcnn(nn.Module):
    """ Classification architecture
    [B, F, N] -> [B, nCls]
    """
    def __init__(self, conf):
        super(dgcnn, self).__init__()
        self.ec0 = edgeConv(conf.Fin, 64, conf.k)
        self.ec1 = edgeConv(64, 64, conf.k)
        self.ec2 = edgeConv(64, 64, conf.k)
        self.ec3 = edgeConv(64, 128, conf.k)
        self.conv0 = nn_utils.conv1dbr(128+64*3, 1024, 1)
        self.fc0 = nn_utils.fcbr(1024, 512)
        self.fc1 = nn_utils.fcbr(512, 256)
        self.fc2 = nn_utils.fcbr(256, conf.nCls)


    def forward(self, x):
        B, N, Fin = x.shape
        x = self.ec0(x) # [B, 64, N]
        x1 = x
        x = self.ec1(x) 
        x2 = x
        x = self.ec2(x)
        x3 = x
        x = self.ec3(x) # [B, 128, N]
        x4 = x

        x = torch.cat((x1, x2, x3, x4), dim=1) # [B, 3*64+128, N]

        x = self.conv0(x) # [B, 1024, N]

        x, _ = torch.max(x, 2) # [B, 1024]

        x = self.fc0(x)
        x = self.fc1(x)
        x = self.fc2(x) # [B, nCls]
        return x

