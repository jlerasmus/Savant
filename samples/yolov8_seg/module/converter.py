"""YOLOv8-seg postprocessing (converter)."""

from typing import Any, List, Optional, Tuple

import cv2
import numba as nb
import numpy as np

from savant.base.converter import BaseComplexModelOutputConverter
from savant.deepstream.nvinfer.model import NvInferInstanceSegmentation


class TensorToBBoxSegConverter(BaseComplexModelOutputConverter):
    """YOLOv8-seg output converter.

    :param confidence_threshold: confidence threshold (pre-cluster-threshold)
    :param nms_iou_threshold: nms iou threshold
    :param top_k: leave no more than top K bboxes with maximum confidence
    """

    def __init__(
        self,
        confidence_threshold: float = 0.5,
        nms_iou_threshold: float = 0.45,
        top_k: int = 300,
    ):
        self.confidence_threshold = confidence_threshold
        self.nms_iou_threshold = nms_iou_threshold
        self.top_k = top_k
        super().__init__()

    def __call__(
        self,
        *output_layers: np.ndarray,
        model: NvInferInstanceSegmentation,
        roi: Tuple[float, float, float, float],
    ) -> Optional[Tuple[np.ndarray, List[List[Tuple[str, Any, float]]]]]:
        """Converts model output layer tensors to bbox/seg tensors.

        :param output_layers: Output layer tensor
        :param model: Model definition, required parameters: input tensor shape,
            maintain_aspect_ratio
        :param roi: [top, left, width, height] of the rectangle
            on which the model infers
        :return: a combination of :py:class:`.BaseObjectModelOutputConverter` and
            corresponding segmentation masks
        """

        tensors, masks = _postproc(
            output=output_layers[0],
            protos=output_layers[1],
            num_detected_classes=model.output.num_detected_classes,
            nms_iou_threshold=self.nms_iou_threshold,
            confidence_threshold=self.confidence_threshold,
            top_k=self.top_k,
        )

        if tensors.shape[0] == 0:
            return

        roi_left, roi_top, roi_width, roi_height = roi

        if model.input.maintain_aspect_ratio:
            ratio_x = ratio_y = max(
                roi_width / model.input.width,
                roi_height / model.input.height,
            )
        else:
            ratio_x = roi_width / model.input.width
            ratio_y = roi_height / model.input.height

        # scale & shift bboxes
        tensors[:, [2, 4]] *= ratio_x
        tensors[:, [3, 5]] *= ratio_y
        tensors[:, 2] += roi_left
        tensors[:, 3] += roi_top

        # scale masks (transpose to use cv2.resize)
        masks = masks.transpose((1, 2, 0))
        mask_width = int(ratio_x * model.input.width)
        mask_height = int(ratio_y * model.input.height)
        masks = cv2.resize(masks, (mask_width, mask_height), cv2.INTER_LINEAR)
        masks = (
            masks.transpose((2, 0, 1)) if len(masks.shape) == 3 else masks[None, ...]
        )

        masks = masks > 0.5

        mask_list = [
            [
                (
                    model.output.attributes[0].name,
                    masks[
                        i,
                        max(0, int(tensors[i, 3] - tensors[i, 5] / 2)) : min(
                            mask_height, int(tensors[i, 3] + tensors[i, 5] / 2)
                        ),
                        max(0, int(tensors[i, 2] - tensors[i, 4] / 2)) : min(
                            mask_width, int(tensors[i, 2] + tensors[i, 4] / 2)
                        ),
                    ],
                    1.0,
                )
            ]
            for i in range(len(masks))
        ]

        return tensors, mask_list


jit_kwargs = dict(nogil=True, cache=True, fastmath=True)


@nb.njit('Tuple((u2[:], f4[:]))(f4[:, :])', **jit_kwargs)
def _parse_scores(scores: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    class_ids = np.empty(scores.shape[0], dtype=np.uint16)
    confidences = np.empty(scores.shape[0], dtype=scores.dtype)
    for i in range(scores.shape[0]):
        class_ids[i] = scores[i, :].argmax()
        confidences[i] = scores[i, class_ids[i]]
    return class_ids, confidences


@nb.njit('f4[:, ::1](f4[:, :])', **jit_kwargs)
def sigmoid(a: np.ndarray) -> np.ndarray:
    ones = np.ones(a.shape, dtype=np.float32)
    return np.divide(ones, (ones + np.exp(-a)))


@nb.njit('u4[:](f4[:, :], f4[:], f4, u2)', **jit_kwargs)
def nms_cpu(
    bboxes: np.ndarray, confidences: np.ndarray, threshold: float, top_k: int = 300
) -> np.ndarray:
    """Performs non-maximum suppression (NMS) on the boxes according
    to their intersection-over-union (IoU). NumPy (CPU) version.

    :param bboxes: Boxes to perform NMS on.
        They are expected to be in (xc, yc, width, height) format.
    :param confidences: Scores for each one of the boxes.
    :param threshold: IoU threshold.
        Discards all overlapping boxes with IoU > threshold.
    :param top_k: Returns only K with max confidence/score.
    :return: Indices of the boxes that have been kept by NMS,
        sorted in decreasing order of scores.
    """
    x_left = bboxes[:, 0]
    y_top = bboxes[:, 1]
    x_right = bboxes[:, 0] + bboxes[:, 2]
    y_bottom = bboxes[:, 1] + bboxes[:, 3]

    areas = (x_right - x_left) * (y_bottom - y_top)
    order = confidences.argsort()[::-1].astype(np.uint32)

    mask = np.zeros((min(bboxes.shape[0], top_k),), dtype=np.uint32)
    mask_idx = 0
    while order.size > 0 and mask_idx < top_k:
        idx_self = order[0]
        idx_other = order[1:]

        mask[mask_idx] = idx_self
        mask_idx += 1

        xx1 = np.maximum(x_left[idx_self], x_left[idx_other])
        yy1 = np.maximum(y_top[idx_self], y_top[idx_other])
        xx2 = np.minimum(x_right[idx_self], x_right[idx_other])
        yy2 = np.minimum(y_bottom[idx_self], y_bottom[idx_other])

        width = np.maximum(0.0, xx2 - xx1)
        height = np.maximum(0.0, yy2 - yy1)
        inter = width * height

        over = inter / (areas[idx_self] + areas[idx_other] - inter)

        indices = np.where(over <= threshold)[0]
        order = order[indices + 1]

    return mask[:mask_idx]


@nb.njit(
    'Tuple((f4[:, ::1], f4[:, :, ::1]))(f4[:, ::1], f4[:, :, ::1], u2, f4, f4, u2)',
    **jit_kwargs,
)
def _postproc(
    output: np.ndarray,
    protos: np.ndarray,
    num_detected_classes: int,
    nms_iou_threshold: float,
    confidence_threshold: float,
    top_k: int,
) -> Tuple[np.ndarray, np.ndarray]:
    # bbox postprocessing
    # shape(4 + num_classes + num_masks, num_boxes) => shape(num_boxes, ...)
    output = output.transpose(-1, -2)

    bboxes = output[:, :4]  # xc, yc, width, height
    scores = output[:, 4 : 4 + num_detected_classes]
    # class_ids = np.argmax(scores, axis=-1)
    # confidences = np.max(scores, axis=-1)
    class_ids, confidences = _parse_scores(scores)
    masks = output[:, 4 + num_detected_classes :]

    # filter by confidence
    if confidence_threshold and bboxes.shape[0] > 0:
        conf_mask = confidences > confidence_threshold
        bboxes = bboxes[conf_mask]
        class_ids = class_ids[conf_mask]
        confidences = confidences[conf_mask]
        masks = masks[conf_mask]

    # nms, class agnostic (all classes are treated as one)
    if nms_iou_threshold and bboxes.shape[0] > 0:
        nms_mask = nms_cpu(bboxes, confidences, nms_iou_threshold, top_k)
        bboxes = bboxes[nms_mask]
        class_ids = class_ids[nms_mask]
        confidences = confidences[nms_mask]
        masks = masks[nms_mask]

    # select top k (no nms applied)
    if bboxes.shape[0] > top_k:
        top_k_mask = np.argpartition(confidences, -top_k)[-top_k:]
        bboxes = bboxes[top_k_mask]
        class_ids = class_ids[top_k_mask]
        confidences = confidences[top_k_mask]
        masks = masks[top_k_mask]

    if bboxes.shape[0] == 0:
        return np.empty((0, 0), dtype=np.float32), np.empty((0, 0, 0), dtype=np.float32)

    tensors = np.empty((bboxes.shape[0], 6), dtype=np.float32)
    tensors[:, 0] = class_ids
    tensors[:, 1] = confidences
    tensors[:, 2:] = bboxes

    # proto masks postprocessing
    mask_dim, mask_height, mask_width = protos.shape
    masks = sigmoid(np.ascontiguousarray(masks) @ protos.reshape(mask_dim, -1))
    masks = masks.reshape(-1, mask_height, mask_width)

    return tensors, masks
