"""Buffer processor for DeepStream pipeline."""

from heapq import heappop, heappush
from queue import Queue
from typing import Dict, Iterator, List, NamedTuple, Optional

import pyds
from pygstsavantframemeta import (
    gst_buffer_get_savant_batch_meta,
    gst_buffer_get_savant_frame_meta,
    nvds_frame_meta_get_nvds_savant_frame_meta,
)
from savant_rs.match_query import MatchQuery
from savant_rs.pipeline2 import VideoPipeline
from savant_rs.primitives import (
    VideoFrame,
    VideoFrameContent,
    VideoFrameTranscodingMethod,
    VideoFrameTransformation,
)
from savant_rs.primitives.geometry import BBox, RBBox
from savant_rs.utils import VideoObjectBBoxTransformation
from savant_rs.utils.symbol_mapper import (
    build_model_object_key,
    get_object_id,
    parse_compound_key,
)

from savant.api.parser import parse_attribute_value
from savant.gstreamer import Gst  # noqa:F401
from savant.gstreamer.buffer_processor import GstBufferProcessor
from savant.gstreamer.codecs import Codec, CodecInfo
from savant.meta.constants import PRIMARY_OBJECT_KEY, UNTRACKED_OBJECT_ID
from savant.meta.type import ObjectSelectionType
from savant.utils.sink_factories import SinkVideoFrame
from savant.utils.source_info import SourceInfo, SourceInfoRegistry

from .source_output import SourceOutput, SourceOutputOnlyMeta, SourceOutputWithFrame
from .utils.attribute import nvds_add_attr_meta_to_obj
from .utils.iterator import nvds_frame_meta_iterator
from .utils.object import nvds_add_obj_meta_to_frame
from .utils.surface import get_nvds_buf_surface


class _OutputFrame(NamedTuple):
    """Output frame with its metadata."""

    idx: int
    pts: int
    dts: Optional[int]
    frame: Optional[bytes]
    codec: Optional[CodecInfo]
    keyframe: bool


class _PendingFrame(NamedTuple):
    frame_id: int
    previous_frame_id: int
    frame: SinkVideoFrame


class NvDsBufferProcessor(GstBufferProcessor):
    """Buffer processor for Nvidia DeepStream pipeline."""

    def __init__(
        self,
        queue: Queue,
        sources: SourceInfoRegistry,
        video_pipeline: VideoPipeline,
        pass_through_mode: bool,
    ):
        """Buffer processor for DeepStream pipeline.

        :param queue: Queue for output data.
        :param sources: Source info registry.
        :param video_pipeline: Video pipeline.
        :param pass_through_mode: Video pass through mode.
        """

        super().__init__(queue)
        self._sources = sources
        self._queue = queue
        self._video_pipeline = video_pipeline
        self._pass_through_mode = pass_through_mode

        self._last_frame_id: Dict[str, int] = {}
        self._pending_frames: Dict[str, List[_PendingFrame]] = {}

    def prepare_input(self, buffer: Gst.Buffer):
        """Input meta processor.

        :param buffer: gstreamer buffer that is being processed.
        """

        self.logger.debug('Preparing input for buffer with PTS %s.', buffer.pts)
        savant_batch_meta = gst_buffer_get_savant_batch_meta(buffer)
        if savant_batch_meta is None:
            # TODO: add VideoFrame to VideoPipeline?
            self.logger.warning(
                'Failed to prepare input for batch at buffer %s. '
                'Batch has no Savant Frame Meta.',
                buffer.pts,
            )
            return

        batch_id = savant_batch_meta.idx
        nvds_batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buffer))
        for nvds_frame_meta in nvds_frame_meta_iterator(nvds_batch_meta):
            savant_frame_meta = nvds_frame_meta_get_nvds_savant_frame_meta(
                nvds_frame_meta
            )
            if savant_frame_meta is None:
                # TODO: add VideoFrame to VideoPipeline?
                self.logger.warning(
                    'Failed to prepare input for frame %s at buffer %s. '
                    'Frame has no Savant Frame Meta.',
                    nvds_frame_meta.buf_pts,
                    buffer.pts,
                )
                continue

            frame_idx = savant_frame_meta.idx
            video_frame, video_frame_span = self._video_pipeline.get_batched_frame(
                batch_id,
                frame_idx,
            )
            with video_frame_span.nested_span('prepare-input'):
                self._prepare_input_frame(
                    frame_idx=frame_idx,
                    nvds_batch_meta=nvds_batch_meta,
                    nvds_frame_meta=nvds_frame_meta,
                    video_frame=video_frame,
                )

    def _prepare_input_frame(
        self,
        frame_idx: int,
        nvds_batch_meta: pyds.NvDsBatchMeta,
        nvds_frame_meta: pyds.NvDsFrameMeta,
        video_frame: VideoFrame,
    ):
        frame_pts = nvds_frame_meta.buf_pts
        self.logger.debug(
            'Preparing input for frame of source %s with IDX %s and PTS %s.',
            video_frame.source_id,
            frame_idx,
            frame_pts,
        )

        # full frame primary object by default
        source_info = self._sources.get_source(video_frame.source_id)
        self._add_transformations(
            frame_idx=frame_idx,
            video_frame=video_frame,
            source_info=source_info,
        )
        scale_factor_x = source_info.processing_width / source_info.src_resolution.width
        scale_factor_y = (
            source_info.processing_height / source_info.src_resolution.height
        )
        primary_bbox = BBox(
            source_info.processing_width / 2,
            source_info.processing_height / 2,
            source_info.processing_width,
            source_info.processing_height,
        )
        self.logger.debug(
            'Init primary bbox for frame with PTS %s: %s', frame_pts, primary_bbox
        )

        all_nvds_obj_metas = {}
        # add external objects to nvds meta
        for obj_meta in video_frame.access_objects(MatchQuery.idle()):
            obj_key = build_model_object_key(obj_meta.namespace, obj_meta.label)

            bbox = obj_meta.detection_box
            if isinstance(bbox, RBBox) and not bbox.angle:
                bbox = BBox(*bbox.as_xcycwh())

            # skip primary object for now, will be added later
            if obj_key == PRIMARY_OBJECT_KEY:
                # if not a full frame then correct primary object
                if not bbox.almost_eq(primary_bbox, 1e-6):
                    primary_bbox = bbox
                    primary_bbox.scale(scale_factor_x, scale_factor_y)
                    self.logger.debug(
                        'Corrected primary bbox for frame with PTS %s: %s',
                        frame_pts,
                        primary_bbox,
                    )
                continue

            model_name, label = parse_compound_key(obj_key)
            model_uid, class_id = get_object_id(model_name, label)
            if isinstance(bbox, RBBox):
                selection_type = ObjectSelectionType.ROTATED_BBOX
            else:
                selection_type = ObjectSelectionType.REGULAR_BBOX

            bbox.scale(scale_factor_x, scale_factor_y)
            if source_info.padding:
                bbox.left += source_info.padding.left
                bbox.top += source_info.padding.top

            track_id = obj_meta.track_id
            if track_id is None:
                track_id = UNTRACKED_OBJECT_ID
            # create nvds obj meta
            nvds_obj_meta = nvds_add_obj_meta_to_frame(
                batch_meta=nvds_batch_meta,
                frame_meta=nvds_frame_meta,
                selection_type=selection_type,
                class_id=class_id,
                gie_uid=model_uid,
                bbox=bbox,
                confidence=obj_meta.confidence,
                obj_label=obj_key,
                object_id=track_id,
            )

            # save nvds obj meta ref in case it is some other obj's parent
            # and save nvds obj meta ref in case it has a parent
            # this is done to avoid one more full iteration of frame's objects
            # because the parent meta may be created after the child
            all_nvds_obj_metas[obj_meta.id] = nvds_obj_meta

            for namespace, name in obj_meta.attributes:
                attr = obj_meta.get_attribute(namespace, name)
                value = attr.values[0]
                nvds_add_attr_meta_to_obj(
                    frame_meta=nvds_frame_meta,
                    obj_meta=nvds_obj_meta,
                    element_name=namespace,
                    name=name,
                    value=parse_attribute_value(value),
                    confidence=value.confidence,
                )

        # finish configuring obj metas by assigning the parents
        # TODO: fix query to iterate only objects with children
        for parent in video_frame.access_objects(MatchQuery.idle()):
            for child in video_frame.get_children(parent.id):
                all_nvds_obj_metas[child.id].parent = all_nvds_obj_metas[parent.id]

        video_frame.clear_objects()

        # add primary frame object
        model_name, label = parse_compound_key(PRIMARY_OBJECT_KEY)
        model_uid, class_id = get_object_id(model_name, label)
        if source_info.padding:
            primary_bbox.xc += source_info.padding.left
            primary_bbox.yc += source_info.padding.top
        self.logger.debug(
            'Add primary object to frame meta with PTS %s, bbox: %s',
            frame_pts,
            primary_bbox,
        )
        nvds_add_obj_meta_to_frame(
            nvds_batch_meta,
            nvds_frame_meta,
            ObjectSelectionType.REGULAR_BBOX,
            class_id,
            model_uid,
            primary_bbox,
            # confidence should be bigger than tracker minDetectorConfidence
            # to prevent the tracker from deleting the object
            # use tracker display-tracking-id=0 to avoid labelling
            0.999,
            PRIMARY_OBJECT_KEY,
        )

        nvds_frame_meta.bInferDone = True  # required for tracker (DS 6.0)

    def _add_transformations(
        self,
        frame_idx: Optional[int],
        video_frame: VideoFrame,
        source_info: SourceInfo,
    ):
        if self._pass_through_mode:
            return

        self.logger.debug(
            'Adding transformations for frame of source %s with IDX %s and PTS %s.',
            video_frame.source_id,
            frame_idx,
            video_frame.pts,
        )

        scale_transformation = self._get_scale_transformation(video_frame, source_info)
        if scale_transformation is not None:
            self.logger.debug(
                'Adding scale transformation %s for frame of source %s '
                'with IDX %s and PTS %s.',
                scale_transformation,
                video_frame.source_id,
                frame_idx,
                video_frame.pts,
            )
            video_frame.add_transformation(scale_transformation)

        padding_transformation = self._get_padding_transformation(source_info)
        if padding_transformation is not None:
            self.logger.debug(
                'Adding padding transformation %s for frame of source %s '
                'with IDX %s and PTS %s.',
                padding_transformation,
                video_frame.source_id,
                frame_idx,
                video_frame.pts,
            )
            video_frame.add_transformation(padding_transformation)

    def _get_scale_transformation(
        self,
        video_frame: VideoFrame,
        source_info: SourceInfo,
    ) -> Optional[VideoFrameTransformation]:
        """Check if scale transformation is needed and add it to the frame."""

        if video_frame.transformations:
            last_transformation = video_frame.transformations[-1]
            if last_transformation.is_scale:
                last_transformation_size = last_transformation.as_scale
            elif last_transformation.is_initial_size:
                last_transformation_size = last_transformation.as_initial_size
            elif last_transformation.is_resulting_size:
                last_transformation_size = last_transformation.as_resulting_size
            else:
                last_transformation_size = 0, 0

            if last_transformation_size == (
                source_info.processing_width,
                source_info.processing_height,
            ):
                return

        return VideoFrameTransformation.scale(
            source_info.processing_width,
            source_info.processing_height,
        )

    def _get_padding_transformation(
        self,
        source_info: SourceInfo,
    ) -> Optional[VideoFrameTransformation]:
        if source_info.padding and source_info.padding.keep:
            return VideoFrameTransformation.padding(
                source_info.padding.left,
                source_info.padding.top,
                source_info.padding.right,
                source_info.padding.bottom,
            )

    def prepare_output(
        self,
        buffer: Gst.Buffer,
        source_info: SourceInfo,
    ) -> SinkVideoFrame:
        """Enqueue output messages based on frame meta.

        :param buffer: gstreamer buffer that is being processed.
        :param source_info: output source info
        """

        self.logger.debug(
            'Preparing output for buffer with PTS %s and DTS %s for source %s.',
            buffer.pts,
            buffer.dts,
            source_info.source_id,
        )
        for output_frame in self._iterate_output_frames(buffer, source_info):
            sink_video_frame = self._build_sink_video_frame(output_frame, source_info)
            for frame_idx, sink_message in self._fix_frames_order(
                source_info.source_id,
                output_frame.idx,
                sink_video_frame,
            ):
                yield self._delete_frame_from_pipeline(frame_idx, sink_message)

    def _fix_frames_order(
        self,
        source_id: str,
        frame_id: int,
        sink_video_frame: SinkVideoFrame,
    ):
        """Fix the order of the frames if needed.

        It is needed when pass-through mode is enabled and
        the source stream contains B-frames.
        """

        if not self._pass_through_mode:
            self.logger.trace(
                'Pushing frame of source %s with IDX %s, PTS %s and DTS %s.',
                source_id,
                frame_id,
                sink_video_frame.video_frame.pts,
                sink_video_frame.video_frame.dts,
            )
            yield frame_id, sink_video_frame
            return

        pending_frames = self._pending_frames.get(source_id, [])
        if sink_video_frame.video_frame.keyframe:
            # Normally pending_frames should be empty here. But in the case if
            # some frames are lost in the pipeline we need to send them before
            # the keyframe to prevent pipeline from hanging.
            for pending_frame in pending_frames:
                self.logger.trace(
                    'Pushing frame of source %s with IDX %s, PTS %s and DTS %s.',
                    source_id,
                    pending_frame.frame_id,
                    pending_frame.frame.video_frame.pts,
                    pending_frame.frame.video_frame.dts,
                )
                yield pending_frame.frame_id, pending_frame.frame
            self.logger.trace(
                'Pushing frame of source %s with IDX %s, PTS %s and DTS %s.',
                source_id,
                frame_id,
                sink_video_frame.video_frame.pts,
                sink_video_frame.video_frame.dts,
            )
            yield frame_id, sink_video_frame
            self._pending_frames[source_id] = []
            self._last_frame_id[source_id] = frame_id
            return

        last_frame_id = self._last_frame_id.get(source_id)
        try:
            previous_frame_id = sink_video_frame.video_frame.previous_frame_seq_id
        except ValueError:
            self.logger.warning(
                'Failed to get previous frame ID for frame %s from source %s.',
                frame_id,
                source_id,
            )
            previous_frame_id = None
        self.logger.trace(
            'Frame %s from source %s has previous frame ID %s. Last frame ID is %s.',
            frame_id,
            previous_frame_id,
            last_frame_id,
        )

        if previous_frame_id is not None:
            if previous_frame_id == last_frame_id:
                last_frame_id = frame_id
                self.logger.trace(
                    'Pushing frame of source %s with IDX %s, PTS %s and DTS %s.',
                    source_id,
                    frame_id,
                    sink_video_frame.video_frame.pts,
                    sink_video_frame.video_frame.dts,
                )
                yield frame_id, sink_video_frame
            else:
                self.logger.trace(
                    'Storing frame of source %s with IDX %s, '
                    'PTS %s and DTS %s in a buffer.',
                    source_id,
                    frame_id,
                    sink_video_frame.video_frame.pts,
                    sink_video_frame.video_frame.dts,
                )
                heappush(
                    pending_frames,
                    _PendingFrame(
                        frame_id=frame_id,
                        previous_frame_id=previous_frame_id,
                        frame=sink_video_frame,
                    ),
                )
        else:
            last_frame_id = frame_id
            self.logger.trace(
                'Pushing frame of source %s with IDX %s, PTS %s and DTS %s.',
                source_id,
                frame_id,
                sink_video_frame.video_frame.pts,
                sink_video_frame.video_frame.dts,
            )
            yield frame_id, sink_video_frame

        while pending_frames and pending_frames[0].previous_frame_id == last_frame_id:
            pending_frame = heappop(pending_frames)
            self.logger.trace(
                'Pushing frame of source %s with IDX %s, PTS %s and DTS %s. '
                '%s pending frames left.',
                source_id,
                pending_frame.frame_id,
                pending_frame.frame.video_frame.pts,
                pending_frame.frame.video_frame.dts,
                len(pending_frames),
            )
            yield pending_frame.frame_id, pending_frame.frame
            last_frame_id = pending_frame.frame_id

        self._last_frame_id[source_id] = last_frame_id
        self._pending_frames[source_id] = pending_frames

    def _build_sink_video_frame(
        self,
        output_frame: _OutputFrame,
        source_info: SourceInfo,
    ) -> SinkVideoFrame:
        self.logger.debug(
            'Preparing output for frame of source %s with IDX %s, PTS %s and DTS %s.',
            source_info.source_id,
            output_frame.idx,
            output_frame.pts,
            output_frame.dts,
        )

        video_frame: VideoFrame
        video_frame, video_frame_span = self._video_pipeline.get_independent_frame(
            output_frame.idx,
        )
        with video_frame_span.nested_span('prepare_output'):
            if self._pass_through_mode:
                if video_frame.content.is_internal():
                    content = video_frame.content.get_data()
                    self.logger.debug(
                        'Pass-through mode is enabled. '
                        'Sending frame with IDX %s to sink without any changes. '
                        '%s bytes.',
                        output_frame.idx,
                        len(content),
                    )
                    video_frame.content = VideoFrameContent.none()
                else:
                    self.logger.debug(
                        'Pass-through mode is enabled. '
                        'Sending frame with IDX %s to sink without any changes. '
                        'No content.',
                        output_frame.idx,
                    )
                    content = None
                video_frame.transcoding_method = VideoFrameTranscodingMethod.Copy

            else:
                video_frame.width = source_info.output_width
                video_frame.height = source_info.output_height
                video_frame.dts = output_frame.dts
                if output_frame.codec is not None:
                    video_frame.codec = output_frame.codec.name
                video_frame.keyframe = output_frame.keyframe
                content = output_frame.frame
                video_frame.transcoding_method = VideoFrameTranscodingMethod.Encoded

            self._transform_geometry(source_info, video_frame)

        return SinkVideoFrame(video_frame=video_frame, frame=content)

    def _delete_frame_from_pipeline(
        self,
        frame_idx: int,
        sink_video_frame: SinkVideoFrame,
    ) -> SinkVideoFrame:
        spans = self._video_pipeline.delete(frame_idx)
        span_context = spans[frame_idx].propagate()
        return sink_video_frame._replace(span_context=span_context)

    def _transform_geometry(self, source_info: SourceInfo, video_frame: VideoFrame):
        if self._pass_through_mode and (
            video_frame.width != source_info.processing_width
            or video_frame.height != source_info.processing_height
        ):
            video_frame.transform_geometry(
                [
                    VideoObjectBBoxTransformation.scale(
                        video_frame.width / source_info.processing_width,
                        video_frame.height / source_info.processing_height,
                    )
                ]
            )

    def on_eos(self, source_info: SourceInfo):
        """Pipeline EOS handler."""
        pending_frames = self._pending_frames.pop(source_info.source_id, [])
        for pending_frame in pending_frames:
            self.logger.trace(
                'Pushing frame of source %s with IDX %s, PTS %s and DTS %s.',
                source_info.source_id,
                pending_frame.frame_id,
                pending_frame.frame.video_frame.pts,
                pending_frame.frame.video_frame.dts,
            )
            sink_message = self._delete_frame_from_pipeline(
                pending_frame.frame_id,
                pending_frame.frame,
            )
            self._queue.put(sink_message)

    def _iterate_output_frames(
        self,
        buffer: Gst.Buffer,
        source_info: SourceInfo,
    ) -> Iterator[_OutputFrame]:
        """Iterate output frames."""


class NvDsGstBufferProcessor(NvDsBufferProcessor):
    def __init__(
        self,
        queue: Queue,
        sources: SourceInfoRegistry,
        codec: CodecInfo,
        video_pipeline: VideoPipeline,
    ):
        """Buffer processor for DeepStream pipeline.

        :param queue: Queue for output data.
        :param sources: Source info registry.
        :param codec: Codec of the output frames.
        """

        self._codec = codec
        super().__init__(
            queue=queue,
            sources=sources,
            video_pipeline=video_pipeline,
            pass_through_mode=False,
        )

    def _iterate_output_frames(
        self,
        buffer: Gst.Buffer,
        source_info: SourceInfo,
    ) -> Iterator[_OutputFrame]:
        """Iterate output frames from Gst.Buffer."""

        yield self._build_output_frame(
            idx=extract_frame_idx(buffer),
            pts=buffer.pts,
            dts=buffer.dts if buffer.dts != Gst.CLOCK_TIME_NONE else None,
            buffer=buffer,
        )

    def _build_output_frame(
        self,
        idx: Optional[int],
        pts: int,
        dts: Optional[int],
        buffer: Gst.Buffer,
    ) -> _OutputFrame:
        if buffer.get_size() > 0:
            # get encoded frame for output
            frame = buffer.extract_dup(0, buffer.get_size())
        else:
            frame = None
            dts = None
        is_keyframe = not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT)
        return _OutputFrame(
            idx=idx,
            pts=pts,
            dts=dts,
            frame=frame,
            codec=self._codec,
            keyframe=is_keyframe,
        )


class NvDsRawBufferProcessor(NvDsBufferProcessor):
    def __init__(
        self,
        queue: Queue,
        sources: SourceInfoRegistry,
        output_frame: bool,
        video_pipeline: VideoPipeline,
        pass_through_mode: bool,
    ):
        """Buffer processor for DeepStream pipeline.

        :param queue: Queue for output data.
        :param sources: Source info registry.
        :param output_frame: Whether to output frame or not.
        :param video_pipeline: Video pipeline.
        :param pass_through_mode: Video pass through mode.
        """

        self._output_frame = output_frame
        self._codec = Codec.RAW_RGBA.value if output_frame else None
        super().__init__(
            queue=queue,
            sources=sources,
            video_pipeline=video_pipeline,
            pass_through_mode=pass_through_mode,
        )

    def _iterate_output_frames(
        self,
        buffer: Gst.Buffer,
        source_info: SourceInfo,
    ) -> Iterator[_OutputFrame]:
        """Iterate output frames from NvDs batch.

        NvDs batch contains raw RGBA frames. They are all keyframes.
        """

        if buffer.get_size() == 0:
            frame_idx = extract_frame_idx(buffer)
            yield _OutputFrame(
                idx=frame_idx,
                pts=buffer.pts,
                dts=None,  # Raw frames do not have dts
                frame=None,
                codec=self._codec,
                # Any frame is keyframe since it was not encoded
                keyframe=True,
            )
            return

        nvds_batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buffer))
        for nvds_frame_meta in nvds_frame_meta_iterator(nvds_batch_meta):
            # get frame if required for output
            if self._output_frame:
                with get_nvds_buf_surface(buffer, nvds_frame_meta) as np_frame:
                    frame = np_frame.tobytes()
            else:
                frame = None
            savant_frame_meta = nvds_frame_meta_get_nvds_savant_frame_meta(
                nvds_frame_meta
            )
            frame_idx = savant_frame_meta.idx if savant_frame_meta else None
            frame_pts = nvds_frame_meta.buf_pts
            yield _OutputFrame(
                idx=frame_idx,
                pts=frame_pts,
                dts=None,  # Raw frames do not have dts
                frame=frame,
                codec=self._codec,
                # Any frame is keyframe since it was not encoded
                keyframe=True,
            )


def extract_frame_idx(buffer: Gst.Buffer) -> Optional[int]:
    """Extract frame index from the buffer."""

    savant_frame_meta = gst_buffer_get_savant_frame_meta(buffer)
    return savant_frame_meta.idx if savant_frame_meta else None


def create_buffer_processor(
    queue: Queue,
    sources: SourceInfoRegistry,
    source_output: SourceOutput,
    video_pipeline: VideoPipeline,
    pass_through_mode: bool,
):
    """Create buffer processor.

    :param queue: Queue for output data.
    :param sources: Source info registry.
    :param source_output: Source output.
    :param video_pipeline: Video pipeline.
    :param pass_through_mode: Video pass through mode.
    """

    if isinstance(source_output, SourceOutputOnlyMeta) or (
        isinstance(source_output, SourceOutputWithFrame)
        and source_output.codec.name == Codec.RAW_RGBA.value.name
    ):
        return NvDsRawBufferProcessor(
            queue=queue,
            sources=sources,
            output_frame=isinstance(source_output, SourceOutputWithFrame),
            video_pipeline=video_pipeline,
            pass_through_mode=pass_through_mode,
        )

    return NvDsGstBufferProcessor(
        queue=queue,
        sources=sources,
        codec=source_output.codec,
        video_pipeline=video_pipeline,
    )
