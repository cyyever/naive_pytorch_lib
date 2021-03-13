import copy

import torch
from cyy_naive_lib.log import get_logger
from torch.quantization.fuser_method_mappings import \
    DEFAULT_OP_LIST_TO_FUSER_METHOD

from callback import Callback
from model_util import ModelUtil
from trainer import Trainer


class QuantizationTrainer(Callback):
    """
    Training Aware Quantization
    """

    def __init__(
        self,
        replace_layer=True,
    ):
        super().__init__()
        self.__replace_layer = replace_layer
        self.__original_model = None
        self.__replace_model = None
        self.__quantized_model = None

    def _before_execute(self, **kwargs):
        trainer = kwargs["model_executor"]
        self.prepare_quantization(trainer)

    def _after_execute(self, **kwargs):
        trainer = kwargs["model_executor"]
        trainer.model.cpu()
        trainer.model.eval()
        self.__quantized_model = torch.quantization.convert(trainer.model)

    def prepare_quantization(self, trainer: Trainer):
        self.__original_model = trainer.model
        if self.__replace_layer:
            model_util = ModelUtil(copy.deepcopy(self.__original_model))
            # change ReLU6 to ReLU
            if model_util.has_sub_module(torch.nn.modules.activation.ReLU6):
                get_logger().info(
                    "replace torch.nn.modules.activation.ReLU6 to torch.nn.modules.activation.ReLU"
                )
                model_util.change_sub_modules(
                    torch.nn.modules.activation.ReLU6,
                    lambda name, sub_module: torch.nn.modules.activation.ReLU(
                        inplace=sub_module.inplace
                    ),
                )
        else:
            model_util = ModelUtil(copy.deepcopy(self.__original_model))

        if model_util.has_sub_module(torch.quantization.QuantStub):
            quant_model = model_util.model
        else:
            quant_model = torch.quantization.QuantWrapper(model_util.model)
        quant_model.cpu()
        quant_model.qconfig = torch.quantization.get_default_qat_qconfig("fbgemm")

        if hasattr(quant_model, "fuse_model"):
            quant_model.fuse_model()
        else:
            torch.quantization.fuse_modules(
                quant_model,
                QuantizationTrainer.get_fused_modules(quant_model),
                inplace=True,
            )
        torch.quantization.prepare_qat(quant_model, inplace=True)
        get_logger().debug("quant_model is %s", quant_model)
        trainer.set_model(quant_model)

    @property
    def quantized_model(self) -> torch.nn.Module:
        return self.__quantized_model

    def reset_quantized_model(self):
        self.__quantized_model = None

    @staticmethod
    def get_fused_modules(model):
        modules = list(model.named_modules())
        list_of_list = []
        i = 0
        while i < len(modules):
            candidates: set = set(DEFAULT_OP_LIST_TO_FUSER_METHOD.keys())
            j = i
            end_index = None
            while j < len(modules):
                module = modules[j][1]
                new_candidates = set()
                for candidate in candidates:
                    if isinstance(module, candidate[0]):
                        if len(candidate) == 1:
                            end_index = j
                        else:
                            new_candidates.add(candidate[1:])
                if not new_candidates:
                    break
                candidates = new_candidates
                j += 1
            if end_index is not None:
                module_name_list = []
                while i <= end_index:
                    module_name_list.append(modules[i][0])
                    i += 1
                list_of_list.append(module_name_list)
            else:
                i += 1
        get_logger().debug("list_of_list is %s", list_of_list)
        return list_of_list
