"""서로 다른 시야의 8×8 센서를 독립 CNN 가지로 처리한다."""
import torch
from torch import nn


class DualSensorCNN(nn.Module):
    def __init__(self):
        super().__init__()
        def branch():
            return nn.Sequential(nn.Conv2d(3,16,3,padding=1),nn.ReLU(),nn.MaxPool2d(2),
                nn.Conv2d(16,32,3,padding=1),nn.ReLU(),nn.MaxPool2d(2),
                nn.Conv2d(32,32,3,padding=1),nn.ReLU(),nn.Flatten(),nn.Linear(128,32),nn.ReLU())
        self.foot = branch(); self.head_sensor = branch(); self.classifier = nn.Linear(64,6)

    @torch.jit.export
    def features(self, x: torch.Tensor) -> torch.Tensor:
        return torch.cat((self.foot(x[:,0]), self.head_sensor(x[:,1])), dim=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.features(x))
