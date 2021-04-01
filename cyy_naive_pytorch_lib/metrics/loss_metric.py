from tensor import get_batch_size

from .metric import Metric


class LossMetric(Metric):
    def _after_batch(self, **kwargs):
        batch_loss = kwargs.get("batch_loss")
        batch = kwargs.get("batch")
        epoch = kwargs.get("epoch")
        model_executor = kwargs.get("model_executor")
        real_batch_loss = batch_loss
        if model_executor.model_with_loss.is_averaged_loss():
            real_batch_loss *= get_batch_size(model_executor.decode_batch(batch)[0])
        real_batch_loss /= len(model_executor.dataset)
        epoch_loss = self.get_epoch_metric(epoch, "loss")
        if epoch_loss is None:
            epoch_loss = real_batch_loss
        else:
            epoch_loss += real_batch_loss
        self._set_epoch_metric(epoch, "loss", epoch_loss)

    def get_loss(self, epoch):
        return self.get_epoch_metric(epoch, "loss")
