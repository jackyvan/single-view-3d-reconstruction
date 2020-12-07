import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from util.visualize import visualize_sdf


class IFNet(nn.Module):

    def __init__(self, hidden_dim=256):
        super(IFNet, self).__init__()
        self.ifnet_feature_extractor = IFNetFeatureExtractor(32, 64, 128, 128)
        feature_size = (1 + 64 + 128 + 128) * 7
        self.fc_0 = nn.Conv1d(feature_size, hidden_dim * 2, 1)
        self.fc_1 = nn.Conv1d(hidden_dim * 2, hidden_dim, 1)
        self.fc_2 = nn.Conv1d(hidden_dim, hidden_dim, 1)
        self.fc_out = nn.Conv1d(hidden_dim, 1, 1)
        self.actvn = nn.ReLU()

    def forward(self, x, points):
        features = self.ifnet_feature_extractor(x, points)
        shape = features.shape
        features = torch.reshape(features, (shape[0], shape[1] * shape[3], shape[4]))  # (B, featues_per_sample, samples_num)
        # features = torch.cat((features, p_features), dim=1)  # (B, featue_size, samples_num)

        net = self.actvn(self.fc_0(features))
        net = self.actvn(self.fc_1(net))
        net = self.actvn(self.fc_2(net))
        net = self.fc_out(net)
        out = net.squeeze(1)

        return out


class IFNetFeatureExtractor(nn.Module):

    def __init__(self, f1, f2, f3, f4):
        super(IFNetFeatureExtractor, self).__init__()

        self.conv_1 = nn.Conv3d(1, f1, 3, padding=1)  # out: 8
        self.conv_1_1 = nn.Conv3d(f1, f2, 3, padding=1)  # out: 8
        self.conv_2 = nn.Conv3d(f2, f3, 3, padding=1)  # out: 4
        self.conv_2_1 = nn.Conv3d(f3, f4, 3, padding=1)  # out: 4
        self.conv_3 = nn.Conv3d(f4, f4, 3, padding=1)  # out: 2
        self.conv_3_1 = nn.Conv3d(f4, f4, 3, padding=1)  # out: 2
        self.actvn = nn.ReLU()
        self.maxpool = nn.MaxPool3d(2)

        self.conv1_1_bn = nn.BatchNorm3d(f2)
        self.conv2_1_bn = nn.BatchNorm3d(f4)
        self.conv3_1_bn = nn.BatchNorm3d(f4)

        displacment = 0.035
        displacments = []
        displacments.append([0, 0, 0])
        for x in range(3):
            for y in [-1, 1]:
                input = [0, 0, 0]
                input[x] = y * displacment
                displacments.append(input)

        self.displacments = torch.Tensor(displacments)

    def forward(self, x, points):
        p = torch.zeros_like(points)
        p[:, :, 0], p[:, :, 1], p[:, :, 2] = [2 * points[:, :, 2], 2 * points[:, :, 1], 2 * points[:, :, 0]]
        p = p.unsqueeze(1).unsqueeze(1)
        p = torch.cat([p + d for d in self.displacments.to(p.device)], dim=2)  # (B,1,7,num_samples,3)
        feature_0 = F.grid_sample(x, p, align_corners=True)  # out : (B,C (of x), 1,1,sample_num)

        net = self.actvn(self.conv_1(x))
        net = self.actvn(self.conv_1_1(net))
        net = self.conv1_1_bn(net)
        feature_1 = F.grid_sample(net, p, align_corners=True)  # out : (B,C (of x), 1,1,sample_num)
        net = self.maxpool(net)

        net = self.actvn(self.conv_2(net))
        net = self.actvn(self.conv_2_1(net))
        net = self.conv2_1_bn(net)
        feature_2 = F.grid_sample(net, p, align_corners=True)
        net = self.maxpool(net)

        net = self.actvn(self.conv_3(net))
        net = self.actvn(self.conv_3_1(net))
        net = self.conv3_1_bn(net)
        feature_3 = F.grid_sample(net, p, align_corners=True)

        # here every channel corresponse to one feature.

        features = torch.cat((feature_0, feature_1, feature_2, feature_3), dim=1)  # (B, features, 1,7,sample_num)
        return features


def make_3d_grid(bb_min, bb_max, shape):
    size = shape[0] * shape[1] * shape[2]
    pxs = torch.linspace(bb_min[0], bb_max[0], shape[0])
    pys = torch.linspace(bb_min[1], bb_max[1], shape[1])
    pzs = torch.linspace(bb_min[2], bb_max[2], shape[2])

    pxs = pxs.view(-1, 1, 1).expand(*shape).contiguous().view(size)
    pys = pys.view(1, -1, 1).expand(*shape).contiguous().view(size)
    pzs = pzs.view(1, 1, -1).expand(*shape).contiguous().view(size)
    p = torch.stack([pxs, pys, pzs], dim=1)
    return p


def evaluate_network_on_grid(network, x, resolution):
    points_batch_size = 131072
    pointsf = make_3d_grid(
        (-0.5,)*3, (0.5,)*3, (resolution,)*3
    )
    p_split = torch.split(pointsf, points_batch_size)
    values = []
    for pi in p_split:
        pi = pi.unsqueeze(0).to(x.device)
        with torch.no_grad():
            occ_hat = torch.sigmoid(network(x, pi))
        values.append(occ_hat.squeeze(0).detach().cpu())
    value = torch.cat(values, dim=0).numpy()
    value_grid = value.reshape(resolution, resolution, resolution)
    return value_grid


def implicit_to_mesh(network, x, resolution, threshold_p, output_path):
    value_grid = evaluate_network_on_grid(network, x, resolution)
    visualize_sdf(1 - value_grid, output_path, level=threshold_p)