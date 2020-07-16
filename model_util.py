import torch
import torch.nn as nn
import torch.nn.utils.prune as prune

import util


class ModelUtil:
    def __init__(self, model: torch.nn.Module):
        self.model = model

    def get_parameter_list(self):
        return util.parameters_to_vector(self.model.parameters())

    def get_gradient_list(self):
        return util.parameters_to_vector(
            [parameter.grad for parameter in self.model.parameters()]
        )

    def get_parameter_dict(self):
        parameter_dict = dict()
        for name, param in self.model.named_parameters():
            parameter_dict[name] = param.detach().clone()
        return parameter_dict

    def set_attr(self, name: str, value, as_parameter=True):
        model = self.model
        components = name.split(".")
        for i, component in enumerate(components):
            if i + 1 != len(components):
                model = getattr(model, component)
            else:
                if as_parameter:
                    model.register_parameter(component, nn.Parameter(value))
                else:
                    setattr(model, component, value)

    def del_attr(self, name: str):
        model = self.model
        components = name.split(".")
        for i, component in enumerate(components):
            if i + 1 != len(components):
                model = getattr(model, component)
            else:
                delattr(model, component)

    def load_parameters(self, parameters: dict):
        for key, value in parameters.items():
            self.set_model_attr(key, value)

    def get_pruned_parameters(self):
        if not prune.is_pruned(self.model):
            raise RuntimeError("not pruned")
        res = dict()
        for layer_index, layer in enumerate(self.model.modules()):
            for name, parameter in layer.named_parameters(recurse=False):
                if parameter is None:
                    continue
                mask = None
                if name.endswith("_orig"):
                    tmp_name = name[:-5]
                    mask = getattr(layer, tmp_name + "_mask", None)
                    if mask is not None:
                        name = tmp_name
                res[(layer, name)] = (parameter, mask, layer_index)
        return res

    def get_pruning_mask(self):
        if not prune.is_pruned(self.model):
            raise RuntimeError("not pruned")
        return util.parameters_to_vector(
            [v[1] for v in self.get_pruned_parameters().values()]
        )

    def get_sparsity(self):
        none_zero_parameter_num = 0
        parameter_count = 0
        for layer, name in self.get_pruned_parameters():
            parameter_count += len(getattr(layer, name).view(-1))
            none_zero_parameter_num += torch.sum(getattr(layer, name) != 0)
        sparsity = 100 * float(none_zero_parameter_num) / \
            float(parameter_count)
        return (sparsity, none_zero_parameter_num, parameter_count)