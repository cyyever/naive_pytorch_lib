import copy
import random

import torch
import torchtext
import torchvision
from cyy_naive_lib.log import get_logger

try:
    import nvidia.dali
    from nvidia.dali.pipeline import pipeline_def
    from nvidia.dali.plugin.pytorch import (DALIClassificationIterator,
                                            LastBatchPolicy)

    has_dali = True
except ModuleNotFoundError:
    has_dali = False

from dataset_collection import DatasetCollection
from hyper_parameter import HyperParameter
from ml_type import DatasetType, MachineLearningPhase, ModelType


class ExternalInputIterator:
    def __init__(self, dataset, original_dataset, batch_size: int, shuffle: bool):
        self.__dataset = dataset
        self.__original_dataset = copy.copy(original_dataset)
        setattr(self.__original_dataset, "transform", None)
        setattr(self.__original_dataset, "transforms", None)

        assert hasattr(self.__original_dataset, "data")
        if hasattr(dataset, "indices"):
            self.__indices = dataset.indices
        else:
            self.__indices = list(range(len(dataset)))
            assert dataset == original_dataset
        self.__data = original_dataset.data
        self.__targets = original_dataset.targets
        assert len(self.__indices) == len(self.__dataset)
        assert len(self.__dataset) <= len(self.__data)

        self.__tmp_indices = None
        self.__batch_size = batch_size
        self.__shuffle = shuffle
        self.__transform = torchvision.transforms.ToTensor()
        self.__use_iter = False

    def __iter__(self):
        assert not self.__use_iter
        self.__use_iter = True
        self.__reset()
        return self

    def __reset(self):
        self.__tmp_indices = copy.deepcopy(self.__indices)
        self.__tmp_indices.reverse()
        if self.__shuffle:
            random.shuffle(self.__tmp_indices)

    def __next__(self):
        batch = []
        labels = []
        if not self.__tmp_indices:
            self.__reset()
            raise StopIteration()
        for _ in range(self.__batch_size):
            if not self.__tmp_indices:
                break
            idx = self.__tmp_indices.pop()
            # sample = self.__data[idx]
            # if not isinstance(sample, numpy.ndarray):
            #     sample = sample.numpy()
            # sample = self.__transform(sample)
            # label = self.__targets[idx]
            sample, label = self.__original_dataset[idx]
            sample = self.__transform(sample)
            batch.append(sample)
            labels.append(label)
        return (batch, torch.LongTensor(labels))

    def __len__(self):
        return len(self.__dataset)


def get_raw_transformers(obj) -> list:
    raw_transforms = []
    if isinstance(obj, torchvision.transforms.Compose):
        for transform in obj.transforms:
            raw_transforms += get_raw_transformers(transform)
    elif isinstance(obj, torchvision.datasets.vision.StandardTransform):
        raw_transforms += get_raw_transformers(obj.transform)
    else:
        raw_transforms.append(obj)
    assert raw_transforms
    return raw_transforms


if has_dali:

    @pipeline_def
    def create_dali_pipeline(
        dc: DatasetCollection,
        phase: MachineLearningPhase,
        hyper_parameter: HyperParameter,
        device,
    ):
        dataset = dc.get_dataset(phase)
        original_dataset = dc.get_original_dataset(phase)
        is_external_source = False
        if isinstance(original_dataset, torchvision.datasets.folder.ImageFolder):
            samples = original_dataset.samples
            if hasattr(dataset, "indices"):
                samples = [samples[idx] for idx in dataset.indices]
            image_files, labels = nvidia.dali.fn.readers.file(
                files=[s[0] for s in samples],
                labels=[s[1] for s in samples],
                random_shuffle=(phase == MachineLearningPhase.Training),
            )
        else:
            external_source = ExternalInputIterator(
                dataset=dataset,
                original_dataset=original_dataset,
                batch_size=hyper_parameter.batch_size,
                shuffle=(phase == MachineLearningPhase.Training),
            )
            is_external_source = True
            images, labels = nvidia.dali.fn.external_source(
                source=external_source, layout=("CHW", ""), num_outputs=2
            )
            # images = nvidia.dali.fn.decoders.image(images, device="cpu" if device is None else "mixed")

        raw_transforms = get_raw_transformers(original_dataset.transforms)
        raw_transforms = [
            t
            for t in raw_transforms
            if not isinstance(t, torchvision.transforms.transforms.ToTensor)
        ]
        raw_transform_dict = dict(enumerate(raw_transforms))
        get_logger().debug("raw_transforms are %s", raw_transform_dict)
        # crop_size = None
        horizontal_mirror = False
        mean_and_std = None
        raw_transform_dict_copy = copy.copy(raw_transform_dict)
        for idx in sorted(raw_transform_dict_copy):
            transform = raw_transform_dict_copy[idx]
            if isinstance(
                transform, torchvision.transforms.transforms.RandomResizedCrop
            ):
                # ask nvJPEG to preallocate memory for the biggest sample in ImageNet for CPU and GPU to avoid reallocations in runtime
                device_memory_padding = 211025920 if device is not None else 0
                host_memory_padding = 140544512 if device is not None else 0
                images = nvidia.dali.fn.decoders.image_random_crop(
                    image_files,
                    device="cpu" if device is None else "mixed",
                    device_memory_padding=device_memory_padding,
                    host_memory_padding=host_memory_padding,
                    num_attempts=5,
                )
                images = nvidia.dali.fn.resize(
                    images,
                    dtype=nvidia.dali.types.FLOAT,
                    device="cpu" if device is None else "gpu",
                    resize_x=transform.size[0],
                    resize_y=transform.size[1],
                )
                # crop_size = transform.size
                raw_transform_dict.pop(idx)
                continue
            if isinstance(
                transform, torchvision.transforms.transforms.RandomHorizontalFlip
            ):
                horizontal_mirror = nvidia.dali.fn.random.coin_flip(
                    probability=transform.p
                )
                raw_transform_dict.pop(idx)
                continue
            if isinstance(transform, torchvision.transforms.transforms.Normalize):
                mean_and_std = (copy.deepcopy(transform.mean), transform.std)
                raw_transform_dict.pop(idx)
                continue
        assert mean_and_std is not None
        scale = 1.0
        if not is_external_source:
            for idx, m in enumerate(mean_and_std[0]):
                assert 0 <= m <= 1
                mean_and_std[0][idx] = m * 255
            scale = 1.0 / 255
        images = nvidia.dali.fn.crop_mirror_normalize(
            images,
            dtype=nvidia.dali.types.FLOAT,
            output_layout="CHW",
            mean=mean_and_std[0].tolist(),
            std=mean_and_std[1].tolist(),
            scale=scale,
            mirror=horizontal_mirror,
        )

        if raw_transform_dict:
            get_logger().error("remaining raw_transforms are %s", raw_transform_dict)
        assert not raw_transform_dict

        if device is not None:
            labels = labels.gpu()
        return images, labels


def get_dataloader(
    dc: DatasetCollection,
    model_type: ModelType,
    phase: MachineLearningPhase,
    hyper_parameter: HyperParameter,
    device=None,
):
    dataset = dc.get_dataset(phase)
    original_dataset = dc.get_original_dataset(phase)
    if (
        has_dali
        and dc.dataset_type == DatasetType.Vision
        and model_type == ModelType.Classification
        # We use DALI for ImageFolder only
        and isinstance(original_dataset, torchvision.datasets.folder.ImageFolder)
    ):
        get_logger().info("use DALI")
        device_id = -1
        if device is not None:
            device_id = device.index
        pipeline = create_dali_pipeline(
            batch_size=hyper_parameter.batch_size,
            num_threads=2,
            device_id=device_id,
            dc=dc,
            phase=phase,
            hyper_parameter=hyper_parameter,
            device=device,
        )
        pipeline.build()
        return DALIClassificationIterator(
            pipeline,
            auto_reset=True,
            dynamic_shape=True,
            last_batch_policy=LastBatchPolicy.PARTIAL,
            last_batch_padded=True,
        )
    if dc.dataset_type != DatasetType.Text:
        return torch.utils.data.DataLoader(
            dataset,
            batch_size=hyper_parameter.batch_size,
            shuffle=(phase == MachineLearningPhase.Training),
            num_workers=2,
            prefetch_factor=1,
            persistent_workers=False,
            pin_memory=True,
        )
    return torchtext.legacy.data.BucketIterator.splits(
        [dataset],
        batch_size=hyper_parameter.batch_size,
        shuffle=(phase == MachineLearningPhase.Training),
        sort_within_batch=True,
        device=device,
    )[0]
