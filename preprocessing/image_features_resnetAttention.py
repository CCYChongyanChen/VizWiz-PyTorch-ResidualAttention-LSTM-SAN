# TODO : Generalize with device instead of gpu/cpu
import argparse
import time

import h5py
import torch
import torch.backends.cudnn as cudnn
import torch.nn as nn
import torchvision.models as models
import yaml
from torch.autograd import Variable
from tqdm import tqdm
import sys
sys.path.append("..")
from datasets.images import ImageDataset, get_transform

"""residual attention network in pytorch



[1] Fei Wang, Mengqing Jiang, Chen Qian, Shuo Yang, Cheng Li, Honggang Zhang, Xiaogang Wang, Xiaoou Tang

    Residual Attention Network for Image Classification
    https://arxiv.org/abs/1704.06904
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

#"""The Attention Module is built by pre-activation Residual Unit [11] with the
#number of channels in each stage is the same as ResNet [10]."""

class PreActResidualUnit(nn.Module):
    """PreAct Residual Unit
    Args:
        in_channels: residual unit input channel number
        out_channels: residual unit output channel numebr
        stride: stride of residual unit when stride = 2, downsample the featuremap
    """

    def __init__(self, in_channels, out_channels, stride):
        super().__init__()

        bottleneck_channels = int(out_channels / 4)
        self.residual_function = nn.Sequential(
            #1x1 conv
            nn.BatchNorm2d(in_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(in_channels, bottleneck_channels, 1, stride),

            #3x3 conv
            nn.BatchNorm2d(bottleneck_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(bottleneck_channels, bottleneck_channels, 3, padding=1),

            #1x1 conv
            nn.BatchNorm2d(bottleneck_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(bottleneck_channels, out_channels, 1)
        )

        self.shortcut = nn.Sequential()
        if stride != 2 or (in_channels != out_channels):
            self.shortcut = nn.Conv2d(in_channels, out_channels, 1, stride=stride)

    def forward(self, x):

        res = self.residual_function(x)
        shortcut = self.shortcut(x)

        return res + shortcut

class AttentionModule1(nn.Module):

    def __init__(self, in_channels, out_channels, p=1, t=2, r=1):
        super().__init__()
        #"""The hyperparameter p denotes the number of preprocessing Residual
        #Units before splitting into trunk branch and mask branch. t denotes
        #the number of Residual Units in trunk branch. r denotes the number of
        #Residual Units between adjacent pooling layer in the mask branch."""
        assert in_channels == out_channels

        self.pre = self._make_residual(in_channels, out_channels, p)
        self.trunk = self._make_residual(in_channels, out_channels, t)
        self.soft_resdown1 = self._make_residual(in_channels, out_channels, r)
        self.soft_resdown2 = self._make_residual(in_channels, out_channels, r)
        self.soft_resdown3 = self._make_residual(in_channels, out_channels, r)
        self.soft_resdown4 = self._make_residual(in_channels, out_channels, r)

        self.soft_resup1 = self._make_residual(in_channels, out_channels, r)
        self.soft_resup2 = self._make_residual(in_channels, out_channels, r)
        self.soft_resup3 = self._make_residual(in_channels, out_channels, r)
        self.soft_resup4 = self._make_residual(in_channels, out_channels, r)

        self.shortcut_short = PreActResidualUnit(in_channels, out_channels, 1)
        self.shortcut_long = PreActResidualUnit(in_channels, out_channels, 1)

        self.sigmoid = nn.Sequential(
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, kernel_size=1),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, kernel_size=1),
            nn.Sigmoid()
        )

        self.last = self._make_residual(in_channels, out_channels, p)

    def forward(self, x):
        ###We make the size of the smallest output map in each mask branch 7*7 to be consistent
        #with the smallest trunk output map size.
        ###Thus 3,2,1 max-pooling layers are used in mask branch with input size 56 * 56, 28 * 28, 14 * 14 respectively.
        x = self.pre(x)
        input_size = (x.size(2), x.size(3))

        x_t = self.trunk(x)

        #first downsample out 28
        x_s = F.max_pool2d(x, kernel_size=3, stride=2, padding=1)
        x_s = self.soft_resdown1(x_s)

        #28 shortcut
        shape1 = (x_s.size(2), x_s.size(3))
        shortcut_long = self.shortcut_long(x_s)

        #seccond downsample out 14
        x_s = F.max_pool2d(x, kernel_size=3, stride=2, padding=1)
        x_s = self.soft_resdown2(x_s)

        #14 shortcut
        shape2 = (x_s.size(2), x_s.size(3))
        shortcut_short = self.soft_resdown3(x_s)

        #third downsample out 7
        x_s = F.max_pool2d(x, kernel_size=3, stride=2, padding=1)
        x_s = self.soft_resdown3(x_s)

        #mid
        x_s = self.soft_resdown4(x_s)
        x_s = self.soft_resup1(x_s)

        #first upsample out 14
        x_s = self.soft_resup2(x_s)
        x_s = F.interpolate(x_s, size=shape2)
        x_s += shortcut_short

        #second upsample out 28
        x_s = self.soft_resup3(x_s)
        x_s = F.interpolate(x_s, size=shape1)
        x_s += shortcut_long

        #thrid upsample out 54
        x_s = self.soft_resup4(x_s)
        x_s = F.interpolate(x_s, size=input_size)

        x_s = self.sigmoid(x_s)
        x = (1 + x_s) * x_t
        x = self.last(x)

        return x

    def _make_residual(self, in_channels, out_channels, p):

        layers = []
        for _ in range(p):
            layers.append(PreActResidualUnit(in_channels, out_channels, 1))

        return nn.Sequential(*layers)

class AttentionModule2(nn.Module):

    def __init__(self, in_channels, out_channels, p=1, t=2, r=1):
        super().__init__()
        #"""The hyperparameter p denotes the number of preprocessing Residual
        #Units before splitting into trunk branch and mask branch. t denotes
        #the number of Residual Units in trunk branch. r denotes the number of
        #Residual Units between adjacent pooling layer in the mask branch."""
        assert in_channels == out_channels

        self.pre = self._make_residual(in_channels, out_channels, p)
        self.trunk = self._make_residual(in_channels, out_channels, t)
        self.soft_resdown1 = self._make_residual(in_channels, out_channels, r)
        self.soft_resdown2 = self._make_residual(in_channels, out_channels, r)
        self.soft_resdown3 = self._make_residual(in_channels, out_channels, r)

        self.soft_resup1 = self._make_residual(in_channels, out_channels, r)
        self.soft_resup2 = self._make_residual(in_channels, out_channels, r)
        self.soft_resup3 = self._make_residual(in_channels, out_channels, r)

        self.shortcut = PreActResidualUnit(in_channels, out_channels, 1)

        self.sigmoid = nn.Sequential(
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, kernel_size=1),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, kernel_size=1),
            nn.Sigmoid()
        )

        self.last = self._make_residual(in_channels, out_channels, p)

    def forward(self, x):
        x = self.pre(x)
        input_size = (x.size(2), x.size(3))

        x_t = self.trunk(x)

        #first downsample out 14
        x_s = F.max_pool2d(x, kernel_size=3, stride=2, padding=1)
        x_s = self.soft_resdown1(x_s)

        #14 shortcut
        shape1 = (x_s.size(2), x_s.size(3))
        shortcut = self.shortcut(x_s)

        #seccond downsample out 7
        x_s = F.max_pool2d(x, kernel_size=3, stride=2, padding=1)
        x_s = self.soft_resdown2(x_s)

        #mid
        x_s = self.soft_resdown3(x_s)
        x_s = self.soft_resup1(x_s)

        #first upsample out 14
        x_s = self.soft_resup2(x_s)
        x_s = F.interpolate(x_s, size=shape1)
        x_s += shortcut

        #second upsample out 28
        x_s = self.soft_resup3(x_s)
        x_s = F.interpolate(x_s, size=input_size)

        x_s = self.sigmoid(x_s)
        x = (1 + x_s) * x_t
        x = self.last(x)

        return x

    def _make_residual(self, in_channels, out_channels, p):

        layers = []
        for _ in range(p):
            layers.append(PreActResidualUnit(in_channels, out_channels, 1))

        return nn.Sequential(*layers)

class AttentionModule3(nn.Module):

    def __init__(self, in_channels, out_channels, p=1, t=2, r=1):
        super().__init__()

        assert in_channels == out_channels

        self.pre = self._make_residual(in_channels, out_channels, p)
        self.trunk = self._make_residual(in_channels, out_channels, t)
        self.soft_resdown1 = self._make_residual(in_channels, out_channels, r)
        self.soft_resdown2 = self._make_residual(in_channels, out_channels, r)

        self.soft_resup1 = self._make_residual(in_channels, out_channels, r)
        self.soft_resup2 = self._make_residual(in_channels, out_channels, r)

        self.shortcut = PreActResidualUnit(in_channels, out_channels, 1)

        self.sigmoid = nn.Sequential(
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, kernel_size=1),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, kernel_size=1),
            nn.Sigmoid()
        )

        self.last = self._make_residual(in_channels, out_channels, p)

    def forward(self, x):
        x = self.pre(x)
        input_size = (x.size(2), x.size(3))

        x_t = self.trunk(x)

        #first downsample out 14
        x_s = F.max_pool2d(x, kernel_size=3, stride=2, padding=1)
        x_s = self.soft_resdown1(x_s)

        #mid
        x_s = self.soft_resdown2(x_s)
        x_s = self.soft_resup1(x_s)

        #first upsample out 14
        x_s = self.soft_resup2(x_s)
        x_s = F.interpolate(x_s, size=input_size)

        x_s = self.sigmoid(x_s)
        x = (1 + x_s) * x_t
        x = self.last(x)

        return x

    def _make_residual(self, in_channels, out_channels, p):

        layers = []
        for _ in range(p):
            layers.append(PreActResidualUnit(in_channels, out_channels, 1))

        return nn.Sequential(*layers)

class Attention(nn.Module):
    """residual attention netowrk
    Args:
        block_num: attention module number for each stage
    """

    def __init__(self, block_num, class_num=100):

        super().__init__()
        self.pre_conv = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True)
        )

        self.stage1 = self._make_stage(64, 256, block_num[0], AttentionModule1)
        self.stage2 = self._make_stage(256, 512, block_num[1], AttentionModule2)
        self.stage3 = self._make_stage(512, 1024, block_num[2], AttentionModule3)
        self.stage4 = nn.Sequential(
            PreActResidualUnit(1024, 2048, 2),
            PreActResidualUnit(2048, 2048, 1),
            PreActResidualUnit(2048, 2048, 1)
        )
        self.avg = nn.AdaptiveAvgPool2d(1)
        self.linear = nn.Linear(2048, 100)

    def forward(self, x):
        x = self.pre_conv(x)
        x = self.stage1(x)
        x = self.stage2(x)
        x = self.stage3(x)
        x = self.stage4(x)
        x = self.avg(x)
        x = x.view(x.size(0), -1)
        x = self.linear(x)

        return x

    def _make_stage(self, in_channels, out_channels, num, block):

        layers = []
        layers.append(PreActResidualUnit(in_channels, out_channels, 2))

        for _ in range(num):
            layers.append(block(out_channels, out_channels))

        return nn.Sequential(*layers)

class AttentionForVisualization(nn.Module):
    """residual attention netowrk
    Args:
        block_num: attention module number for each stage
    """

    def __init__(self, block_num, class_num=100):

        super().__init__()
        self.pre_conv = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True)
        )

        self.stage1 = self._make_stage(64, 256, block_num[0], AttentionModule1)
        self.stage2 = self._make_stage(256, 512, block_num[1], AttentionModule2)
        self.stage3 = self._make_stage(512, 1024, block_num[2], AttentionModule3)
        self.stage4 = nn.Sequential(
            PreActResidualUnit(1024, 2048, 2),
            PreActResidualUnit(2048, 2048, 1),
            PreActResidualUnit(2048, 2048, 1)
        )
        self.avg = nn.AdaptiveAvgPool2d(1)
        self.linear = nn.Linear(2048, 100)

    def forward(self, inputx):
        x = self.pre_conv(inputx)
        x1 = self.stage1(x)
        x2 = self.stage2(x1)
        x3 = self.stage3(x2)
        x4 = self.stage4(x3)
        x = self.avg(x4)
        x = x.view(x.size(0), -1)
        x = self.linear(x)

        return inputx, x,x1,x2,x3,x4

    def _make_stage(self, in_channels, out_channels, num, block):

        layers = []
        layers.append(PreActResidualUnit(in_channels, out_channels, 2))

        for _ in range(num):
            layers.append(block(out_channels, out_channels))

        return nn.Sequential(*layers)
def attention56():
    return Attention([1, 1, 1])

def attention56forvisual():
    return AttentionForVisualization([1, 1, 1])
def attention92():
    return Attention([1, 2, 3])


weights='/home/cc67459/pytorch-cifar100/checkpoint/attention56/Monday_07_December_2020_21h_04m_02s/attention56-190-regular.pth'

class NetFeatureExtractor(nn.Module):

    def __init__(self):
        super(NetFeatureExtractor, self).__init__()
        self.model = Attention([1, 1, 1])().cuda()
        self.model.load_state_dict(torch.load(weights))
        # PyTorch models available in torch.utils.model_zoo  require an input of size 224x224.
        # This is because of the avgpooling that is fixed 7x7.
        # By using AdaptiveAvgPool2 we can feed to the network images with higher resolution (448x448)
        self.model.avgpool = nn.AdaptiveAvgPool2d(1)

        # Save attention features (tensor)
        def save_att_features(module, input, output):
            self.att_feat = output

        # Save no-attention features (vector)
        def save_noatt_features(module, input, output):
            self.no_att_feat = output

        # This is a forward hook. Is executed each time forward is executed
        self.model.stage4.register_forward_hook(save_att_features)
        self.model.avgpool.register_forward_hook(save_noatt_features)

    def forward(self, x):
        self.model(x)
        return self.no_att_feat, self.att_feat  # [batch_size, 2048], [batch_size, 2048, 14, 14]

class NetFeatureExtractor2(nn.Module):

    def __init__(self):
        super(NetFeatureExtractor2, self).__init__()
        self.model = attention56().cuda()
        self.model.load_state_dict(torch.load(weights))
        # PyTorch models available in torch.utils.model_zoo  require an input of size 224x224.
        # This is because of the avgpooling that is fixed 7x7.
        # By using AdaptiveAvgPool2 we can feed to the network images with higher resolution (448x448)
        self.model.avgpool = nn.AdaptiveAvgPool2d(1)

        # Save attention features (tensor)
        def save_att_features(module, input, output):
            self.att_feat = output

        def save_avg_features(module, input, output):
            self.avg_feat = output

        # This is a forward hook. Is executed each time forward is executed
        self.model.stage4.register_forward_hook(save_att_features)
        self.model.avg.register_forward_hook(save_avg_features)

    def forward(self, x):
        self.model(x)
        return self.att_feat  # [batch_size, 2048], [batch_size, 2048, 14, 14]


def main():
    # Load config yaml file
    parser = argparse.ArgumentParser()
    parser.add_argument('--path_config', default='../config/default.yaml', type=str,
                        help='path to a yaml config file')
    args = parser.parse_args()

    if args.path_config is not None:
        with open(args.path_config, 'r') as handle:
            config = yaml.load(handle)
            config = config['images']

    # Benchmark mode is good whenever your input sizes for your network do not vary
    cudnn.benchmark = True
#     net = nn.DataParallel(NetFeatureExtractor2())
    net = NetFeatureExtractor2().cuda()
#     net.load_state_dict(torch.load(weights))
#     net.stage4.register_forward_hook(printnorm)
    net.eval()
    # Resize, Crop, Normalize
    transform = get_transform(config['img_size'])
    dataset = ImageDataset(config['dir'], transform=transform)

    data_loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=config['preprocess_batch_size'],
        num_workers=config['preprocess_data_workers'],
        shuffle=False,
        pin_memory=True,
    )

    h5_file = h5py.File(config['path_features'], 'w')

    dummy_input = Variable(torch.ones(1, 3, config['img_size'], config['img_size']), volatile=True).cuda()
    dummy_output = net(dummy_input)

    att_features_shape = (
        len(data_loader.dataset),
        dummy_output.size(1),
        dummy_output.size(2),
        dummy_output.size(3)
    )

    noatt_features_shape = (
        len(data_loader.dataset),
        dummy_output.size(1)
    )

    h5_att = h5_file.create_dataset('att', shape=att_features_shape, dtype='float16')
    h5_noatt = h5_file.create_dataset('noatt', shape=noatt_features_shape, dtype='float16')

    # save order of extraction
    dt = h5py.special_dtype(vlen=str)
    img_names = h5_file.create_dataset('img_name', shape=(len(data_loader.dataset),), dtype=dt)

    begin = time.time()
    end = time.time()

    print('Extracting features ...')
    idx = 0
    delta = config['preprocess_batch_size']

    for i, inputs in enumerate(tqdm(data_loader)):
        inputs_img = Variable(inputs['visual'].cuda(async=True), volatile=True)
        att_feat = net(inputs_img)

        # reshape (batch_size, 2048)
#         no_att_feat = no_att_feat.view(-1, 2048)

#         h5_noatt[idx:idx + delta] = no_att_feat.data.cpu().numpy().astype('float16')
        h5_att[idx:idx + delta, :, :] = att_feat.data.cpu().numpy().astype('float16')
        img_names[idx:idx + delta] = inputs['name']

        idx += delta
    h5_file.close()

    end = time.time() - begin

    print('Finished in {}m and {}s'.format(int(end / 60), int(end % 60)))
    print('Created file : ' + config['path_features'])


if __name__ == '__main__':
    main()
