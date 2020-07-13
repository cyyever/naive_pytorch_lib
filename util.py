import os
import PIL
import torch.nn as nn
import torch
import torch.nn.utils.prune as prune
import torchvision


def parameters_to_vector(parameters):
    return nn.utils.parameters_to_vector(
        [parameter.reshape(-1) for parameter in parameters]
    )


def model_parameters_to_vector(model):
    return parameters_to_vector(model.parameters())


def get_model_parameter_dict(model):
    parameter_dict = dict()
    for name, param in model.named_parameters():
        parameter_dict[name] = param.detach().clone()
    return parameter_dict


def set_model_attr(obj, names, value, as_parameter=True):
    if len(names) == 1:
        if as_parameter:
            obj.register_parameter(names[0], nn.Parameter(value))
        else:
            setattr(obj, names[0], value)
    else:
        set_model_attr(getattr(obj, names[0]), names[1:], value, as_parameter)


def del_model_attr(obj, names):
    if len(names) == 1:
        delattr(obj, names[0])
    else:
        del_model_attr(getattr(obj, names[0]), names[1:])


def load_model_parameters(model, parameter_dict):
    for key, value in parameter_dict.items():
        set_model_attr(model, key.split("."), value)


def model_gradients_to_vector(model):
    return parameters_to_vector(
        [parameter.grad for parameter in model.parameters()])


def get_pruned_parameters(model):
    parameters = dict()
    for layer_index, layer in enumerate(model.modules()):
        for name, parameter in layer.named_parameters(recurse=False):
            if parameter is None:
                continue
            mask = None
            if name.endswith("_orig"):
                tmp_name = name[:-5]
                mask = getattr(layer, tmp_name + "_mask", None)
                if mask is not None:
                    name = tmp_name
            parameters[(layer, name)] = (parameter, mask, layer_index)
    return parameters


def get_pruning_mask(model):
    if not prune.is_pruned(model):
        raise RuntimeError("not pruned model")
    return parameters_to_vector(
        [v[1] for v in get_pruned_parameters(model).values()])


def get_model_sparsity(model):
    none_zero_parameter_num = 0
    parameter_count = 0
    for layer, name in get_pruned_parameters(model):
        parameter_count += len(getattr(layer, name).view(-1))
        none_zero_parameter_num += torch.sum(getattr(layer, name) != 0)
    sparsity = 100 * float(none_zero_parameter_num) / float(parameter_count)
    return (sparsity, none_zero_parameter_num, parameter_count)


def save_sample(dataset, idx, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(dataset[idx][0], PIL.Image.Image):
        dataset[idx][0].save(path)
        return
    torchvision.utils.save_image(dataset[idx][0], path)
