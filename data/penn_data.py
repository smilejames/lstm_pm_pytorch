'''
only used for penn_action datasets
'''

import os
import cv2
import torch
import numpy as np
from torch.utils.data import Dataset
from torchvision import transforms
from PIL import Image


class Penn_Data(Dataset):
    def __init__(self, data_dir='Penn_Action/', train=True, transform=None):

        self.input_h = 368
        self.input_w = 368
        self.map_h = 45
        self.map_w = 45

        self.parts_num = 13
        self.seqTrain = 5

        self.gaussian_sigma = 21

        self.transform = transform

        self.train = train
        if self.train is True:
            self.data_dir = data_dir + 'train/'
        else:
            self.data_dir = data_dir + 'test/'

        self.frames_data = os.listdir(self.data_dir)

    def __len__(self):
        return len(self.frames_data)  # number of videos in train or test

    def __getitem__(self, idx):  # get a video sequence
        '''

        :param idx:
        :return:
            images:     Tensor    seqtrain * 3 * width * height
            label_map:  Tensor    46 * 46 * (class+1) * seqtrain
            center_map: Tensor    1 * 368 * 368
        '''
        frames = self.frames_data[idx]
        data = np.load(os.path.join(self.data_dir, frames)).item()

        images, label_map, center_map = self.transformation_penn(data, boxsize=self.input_w, parts_num=13,
                                                               train=self.train)


        center_map = center_map.unsqueeze_(0)

        return images, label_map, center_map

    def transformation_penn(self, data, boxsize=368, parts_num=13, train=True):
        '''
        :param data:
        :param boxsize:
        :param parts_num:
        :param seqTrain:
        :param train:
        :return:
        images tensor seq
        '''
        nframes = data['nframes']                           # 151
        framespath = data['framepath']
        dim = data['dimensions']                            # [360, 480]
        x = data['x']                                       # 151 * 13
        y = data['y']                                       # 151 * 13
        visibility = data['visibility']                     # 151 * 13

        start_index = np.random.randint(0, nframes - 1 - self.seqTrain + 1)  #

        images = torch.zeros(self.seqTrain, 3, dim[0], dim[1])  # tensor seqTrain * 3 * 368 * 368
        label = np.zeros((3, parts_num + 1, self.seqTrain))     # numpy 3
        bbox = np.zeros((self.seqTrain, 4))  # seqTrain * ()    # numpy

        for i in range(self.seqTrain):
            # read image
            img_path = os.path.join(framespath,'%06d' % (start_index + i + 1) + '.jpg')
            img = Image.open(img_path)  # Image
            images[i, :, :, :] = transforms.ToTensor()(img)  # store image

            # read label
            label[0, :-1, i] = x[start_index + i]
            label[1, :-1, i] = y[start_index + i]
            label[2, :-1, i] = visibility[start_index + i]  # 1 * 13
            bbox[i, :] = data['bbox'][start_index + 1]  #

        # adjust label----------
        if train is True:
            # create label for neck to keep consistence of model. But it will be ignored during testing.
            # We interpolate the pos by head(1) and shoulders(2 & 3).
            label[0, -1, :] = 0.5 * label[0, 0, :] + 0.25 * (label[0, 1, :] + label[0, 2, :])
            label[1, -1, :] = 0.5 * label[1, 0, :] + 0.25 * (label[1, 1, :] + label[1, 2, :])
            label[2, -1, :] = np.floor((label[2, 0, :] + label[2, 1, :] + label[2, 2, :]) / 3.0)

        # make the joints not in the figure vis=-1(Do not produce label)
        for i in range(self.seqTrain):  # for each image
            for part in range(0, parts_num + 1):  # for each part
                if self.isNotOnPlane(label[0, part, i], label[1, part, i], dim[1], dim[0]):
                    label[2, part, i] = -1

        # build data set--------
        label_map = self.genLabelMap(label, boxsize=368, stride=8, sigma=7)  # 46 * 46 * (13 + 1) * seq

        center_map = self.genCenterMap(size_w=boxsize, size_h=boxsize, sigma=21, x=boxsize / 2.0, y=boxsize / 2.0)
        center_map = torch.from_numpy(center_map)
        return images, label_map, center_map

    def genCenterMap(self, x, y, sigma, size_w, size_h):
        '''
        generate Gaussian heat map
        :param x: center point
        :param y: center point
        :param sigma:
        :param size_w:
        :param size_h:
        :return: numpy w * h
        '''
        gridy, gridx = np.mgrid[0:size_h, 0:size_w]
        D2 = (gridx - x) ** 2 + (gridy - y) ** 2
        return np.exp(-D2 / 2.0 / sigma / sigma)  # numpy 2d

    def isNotOnPlane(self, x, y, width, height):
        notOn = x < 0.001 or y < 0.001 or x > width or y > height
        return notOn

    def genLabelMap(self, label, boxsize, stride, sigma):
        '''
        generate label heat map for each part
        :param label:       3 * parts_num * seqTrain
        :param boxsize:     368
        :param stride:      8
        :param sigma:       7
        :return:
        seqtrain * (parts_num + 1 ) * label_size * label_size
        5 * 14 * 46 * 46
        '''
        label_size = boxsize // stride               # 368 / 8 = 46
        label_map = torch.zeros(self.seqTrain, self.parts_num + 1, label_size, label_size)

        #
        for k in range(self.seqTrain):              # for each frame
            for i in range(self.parts_num):         # for each parts
                if label[2, i, k] >= 0:             # if exists
                    cx, cy = label[0, i, k], label[1, i, k]  # get the center
                    heat_map = self.genCenterMap(x=cx, y=cy, sigma=sigma, size_w=label_size, size_h=label_size)
                    # build heat map of this part
                else:                               # not exists
                    heat_map = np.zeros((label_size, label_size))
                label_map[k, i, :, :] = torch.from_numpy(np.transpose(heat_map))  #

            # build background
            background = np.ones((label_size, label_size))  #
            for m in range(label_size):
                for n in range(label_size):
                    maxV = max(label_map[k, :, m, n])
                    background[m, n] = max(1 - maxV, 0)
            label_map[k, self.parts_num, :, :] = torch.from_numpy(background)

        return label_map

transform1 = transforms.Compose([
    transforms.ToTensor(), # range [0, 255] -> [0.0,1.0]
    ]
)


# test case
#data = Penn_Data(data_dir='Penn_Action/', transform=transform1)
#images, label_map, center_map = data[1]