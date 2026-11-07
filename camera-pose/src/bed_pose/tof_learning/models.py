"""Small 8x8 spatial encoder and causal temporal encoder for paired ToF frames."""
import torch
from torch import nn


class SpatialEncoder(nn.Module):
    def __init__(self,features=64):
        super().__init__()
        self.network=nn.Sequential(nn.Conv2d(4,32,3,padding=1),nn.GroupNorm(4,32),nn.SiLU(),
            nn.Conv2d(32,64,3,padding=1),nn.GroupNorm(8,64),nn.SiLU(),nn.MaxPool2d(2),
            nn.Conv2d(64,64,3,padding=1),nn.SiLU(),nn.AdaptiveAvgPool2d(1),nn.Flatten(),nn.Linear(64,features),nn.SiLU())
    def forward(self,x):return self.network(x)


class ToFClassifier(nn.Module):
    def __init__(self,kind='cnn',features=64,classes=6):
        super().__init__()
        if kind not in ('cnn','cnn_gru'):raise ValueError('Choose cnn or cnn_gru')
        self.kind=kind;self.encoder=SpatialEncoder(features)
        self.gru=nn.GRU(features,features,batch_first=True) if kind=='cnn_gru' else None
        self.head=nn.Sequential(nn.Dropout(.2),nn.Linear(features,classes))
    def features(self,x):
        b,t,c,h,w=x.shape;spatial=self.encoder(x.reshape(b*t,c,h,w)).reshape(b,t,-1)
        # CNN uses current frame only; GRU consumes past-to-current sequence, never future frames.
        return self.gru(spatial)[1][-1] if self.gru is not None else spatial[:,-1]
    def forward(self,x):return self.head(self.features(x))
