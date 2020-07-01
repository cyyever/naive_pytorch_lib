#!/usr/bin/env python3
import copy
import torch.autograd as autograd

from device import get_device
from util import (
    parameters_to_vector,
    model_parameters_to_vector,
    get_model_parameter_dict,
    load_model_parameters,
)

# from cyy_naive_lib.time_counter import TimeCounter


def get_gradient(model, loss):
    # TODO there is no need to do zero_grad
    model.zero_grad()
    return parameters_to_vector(autograd.grad(loss, model.parameters()))


__cached_model_snapshots = dict()


def clear_cached_model_snapshots():
    global __cached_model_snapshots
    __cached_model_snapshots = dict()


def get_per_sample_gradient(model, loss_fun, inputs, targets, for_train):
    assert inputs
    assert targets
    assert len(inputs) == len(targets)
    assert loss_fun.reduction == "mean" or loss_fun.reduction == "elementwise_mean"

    # get all parameters and names
    parameter_dict = get_model_parameter_dict(model)

    device = get_device()
    batch_size = len(inputs)

    model_class = model.__class__.__name__
    if model_class not in __cached_model_snapshots:
        __cached_model_snapshots[model_class] = list()
    model_snapshots = __cached_model_snapshots[model_class]

    for i in range(0, min(len(model_snapshots), batch_size)):
        parameter_snapshot = copy.deepcopy(parameter_dict)
        load_model_parameters(model_snapshots[i], parameter_snapshot)

    if batch_size > len(model_snapshots):
        for i in range(0, batch_size - len(model_snapshots)):
            model_snapshots.append(copy.deepcopy(model))

    used_models = model_snapshots[:batch_size]
    assert len(used_models) == batch_size
    loss = None
    for i, used_model in enumerate(used_models):
        used_model.zero_grad()
        if for_train:
            used_model.train()
        else:
            used_model.eval()
        used_model.to(device)
        sample_input = torch.stack([inputs[i]])
        sample_target = torch.stack([targets[i]])
        if loss is None:
            loss = loss_fun(used_model(sample_input), sample_target)
        else:
            loss += loss_fun(used_model(sample_input), sample_target)
    loss.backward()
    return [model_parameters_to_vector(m) for m in used_models]


if __name__ == "__main__":
    import torch
    from configuration import get_task_configuration
    from cyy_naive_lib.time_counter import TimeCounter

    trainer = get_task_configuration("MNIST", True)
    training_data_loader = torch.utils.data.DataLoader(
        trainer.training_dataset, batch_size=16, shuffle=True,
    )
    for batch in training_data_loader:
        with TimeCounter() as c:
            get_per_sample_gradient(
                trainer.model, trainer.loss_fun, batch, True)
