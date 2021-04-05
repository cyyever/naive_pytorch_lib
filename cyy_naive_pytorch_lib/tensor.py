from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from cyy_naive_lib.algorithm.mapping_op import get_mapping_values_by_key_order
from cyy_naive_lib.fs.tempdir import TempDir


def cat_tensors_to_vector(tensors) -> torch.Tensor:
    return nn.utils.parameters_to_vector([t.reshape(-1) for t in tensors])


# def concat_dict_values(data: dict) -> torch.Tensor:
#     return cat_tensors_to_vector(get_mapping_values_by_key_order(data))


def load_tensor_dict(data: dict, values: torch.Tensor):
    return load_dict_values(data, values)


def load_dict_values(data: dict, values: torch.Tensor):
    bias = 0
    for name in sorted(data.keys()):
        shape = data[name].shape
        param_element_num = np.prod(shape)
        data[name] = values.narrow(0, bias, param_element_num).view(*shape)
        bias += param_element_num
    assert bias == values.shape[0]
    return data


def get_tensor_serialization_size(data):
    with TempDir():
        torch.save(data, "tensor_data")
        return Path("tensor_data").stat().st_size
