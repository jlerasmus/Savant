"""YOLO base detector postprocessing (converter).
TODO: Add `symmetric-padding` support.
"""

from typing import Optional, Tuple

import numpy as np

from savant.base.converter import BaseObjectModelOutputConverter
from savant.base.model import ObjectModel
from savant.utils.nms import nms_cpu


class TensorToBBoxConverter(BaseObjectModelOutputConverter):
    """YOLO detector output to bbox converter."""

    def __init__(
        self,
        confidence_threshold: float = 0.25,
        nms_iou_threshold: float = 0.0,
        top_k: int = 3000,
        class_ids: Tuple[int] = None,
    ):
        """
        :param confidence_threshold: Select detections with confidence
            greater than specified.
        :param nms_iou_threshold: Class agnostic NMS IoU threshold.
        :param top_k: Maximum number of output detections.
        :param class_ids: Filter detections by class.
        """
        self.confidence_threshold = confidence_threshold
        self.nms_iou_threshold = nms_iou_threshold
        self.top_k = top_k
        self.class_ids = class_ids
        super().__init__()

    def __call__(
        self,
        *output_layers: np.ndarray,
        model: ObjectModel,
        roi: Tuple[float, float, float, float],
    ) -> Optional[np.ndarray]:
        """Converts detector output layer tensor to bbox tensor.

        Converter is suitable for PyTorch YOLOv5/v6/v7/v8 models.
        `output_layers` is assumed to consist of
        either one Nx(num_detected_classes+5) shape tensor,
        or one tensor of shape (num_detected_classes+4)xN,
        or 4 tensors (after NMS) of shapes: 1, Nx4, N, N.

        :param output_layers: Output layer tensor
        :param model: Model definition, required parameters: input tensor shape,
            maintain_aspect_ratio
        :param roi: [top, left, width, height] of the rectangle
            on which the model infers
        :return: BBox tensor (class_id, confidence, xc, yc, width, height, [angle])
            offset by roi upper left and scaled by roi width and height
        """

        assert len(output_layers) in (1, 3, 4)

        if len(output_layers) == 1:
            output = output_layers[0]
            assert model.output.num_detected_classes is not None
            if output.shape[0] == model.output.num_detected_classes + 4:
                output = np.transpose(output)
                scores = output[:, 4:]
            else:
                scores = output[:, 5:] * output[:, 4:5]  # obj_conf * cls_conf
            bboxes = output[:, :4]  # xc, yc, width, height
            class_ids = np.argmax(scores, axis=-1)
            confidences = np.max(scores, axis=-1)

        elif len(output_layers) == 3:
            bboxes, scores, class_ids = output_layers
            confidences = np.max(scores, axis=-1)

        else:
            num_dets, det_boxes, det_scores, det_classes = output_layers
            num = int(num_dets[0])
            bboxes = det_boxes[:num]
            confidences = det_scores[:num]
            class_ids = det_classes[:num]

            # [0..1] -> model.input
            bboxes[:, [0, 2]] *= model.input.width
            bboxes[:, [1, 3]] *= model.input.height

            # (left, top, right, bottom) -> (xc, yc, width, height)
            bboxes[:, 2] -= bboxes[:, 0]
            bboxes[:, 3] -= bboxes[:, 1]
            bboxes[:, 0] += bboxes[:, 2] / 2
            bboxes[:, 1] += bboxes[:, 3] / 2

        # filter by class
        if self.class_ids:
            class_mask = np.isin(class_ids, self.class_ids)
            bboxes = bboxes[class_mask]
            class_ids = class_ids[class_mask]
            confidences = confidences[class_mask]

        # filter by confidence
        if self.confidence_threshold:
            conf_mask = confidences > self.confidence_threshold
            bboxes = bboxes[conf_mask]
            class_ids = class_ids[conf_mask]
            confidences = confidences[conf_mask]

        # TODO: ability to filter by size (width, height) and aspect ratio
        # apply class agnostic NMS (all classes are treated as one)
        if self.nms_iou_threshold > 0 and len(confidences) > 1:
            nms_mask = nms_cpu(bboxes, confidences, self.nms_iou_threshold, self.top_k)
            bboxes = bboxes[nms_mask]
            class_ids = class_ids[nms_mask]
            confidences = confidences[nms_mask]

        # select top k
        elif len(confidences) > self.top_k:
            top_k_mask = np.argpartition(confidences, -self.top_k)[-self.top_k :]
            bboxes = bboxes[top_k_mask]
            class_ids = class_ids[top_k_mask]
            confidences = confidences[top_k_mask]

        roi_left, roi_top, roi_width, roi_height = roi

        # scale
        if model.input.maintain_aspect_ratio:
            bboxes /= min(
                model.input.width / roi_width,
                model.input.height / roi_height,
            )
        else:
            bboxes[:, [0, 2]] /= model.input.width / roi_width
            bboxes[:, [1, 3]] /= model.input.height / roi_height

        # correct xc, yc
        bboxes[:, 0] += roi_left
        bboxes[:, 1] += roi_top

        return np.concatenate(
            (
                class_ids.reshape(-1, 1).astype(np.float32),
                confidences.reshape(-1, 1),
                bboxes,
            ),
            axis=1,
        )
