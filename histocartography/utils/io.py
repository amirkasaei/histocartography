import json
import os
import torch
import numpy as np
from PIL import Image


def complete_path(folder, fname):
    """
    Join a folder and a filename
    """
    return os.path.join(folder, fname)


def get_device(cuda=False):
    """
    Get device (cpu or gpu)
    """
    return'cuda:0' if cuda else 'cpu'


def get_files_in_folder(path, extension):
    """Returns all the file names in a folder, (Relative to the parent folder)
    with a given extension. E.g. if extension == 'svg' it will only return
    svg files.

    Args:
        path (str): path of folder.
        extension (str): type of extension to look for.

    Returns:
        list of file names.
    """
    return [
        f for f in os.listdir(path)
        if os.path.isfile(complete_path(path, f)) and f.endswith(extension)
    ]


def h5_to_tensor(h5_object, device):
    """
    Convert h5 object into torch tensor
    """
    tensor = torch.from_numpy(np.array(h5_object[()])).to(device)
    return tensor


def load_json(fname):
    """
    Load json file as a dict.
    :param fname: (str) path to json
    """
    with open(fname, 'r') as in_config:
        config_params = json.load(in_config)
    return config_params


def load_image(fname):
    """
    Load an image as a PIL image

    Args:
        :param fname: (str) path to image
    """
    image = Image.open(fname)
    return image


def save_image(image, fname):
    image.save(fname)


def show_image(image):
    """
    Show a PIL image
    """
    image.show()


def read_params(fname, verbose=False):
    """
    Config file contains either a simple config set or a list of configs
        (used to run several experiments).

    Args:
        :param fname:
        :param reading_index:
        :param verbose:
        :return: config params
    """
    with open(fname, 'r') as in_config:
        config_params = json.load(in_config)
        if verbose:
            print('\n*** Model config parameters:', config_params)
    return config_params


def get_files_from_text(path,text_path, extension,train_flag):

    list_of_files = os.listdir(text_path) #lists all files in text_path(all text files)
    tumor_type = path.split('/')[-2]#lpath gives tumor type
    tumor = [token for token in tumor_type.split('_') if not token.isdigit()]#tumor_type.split(tumor_type.isdigit)[-1]
    if len(tumor) > 1:
        tumor = ''.join(map(str, tumor))[0]
    else:
        tumor = tumor[0]

    train_files=[]
    valid_files=[]
    for file in list_of_files:
        if train_flag=="train":
            if file.startswith(train_flag) and tumor in file:
                with open("%s/%s" % (text_path, file)) as f:
                    train_files = f.read().split()
                    train_files = [x + extension for x in train_files]
            files = [complete_path(path,g) for g in train_files]
        elif train_flag=="valid":
            if file.startswith(train_flag) and tumor in file:
                with open("%s/%s" % (text_path, file)) as h:
                    valid_files = h.read().split()
                    valid_files = [x + extension for x in valid_files]
            files = [complete_path(path,g) for g in valid_files]

    return files



